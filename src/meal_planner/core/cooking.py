"""Списание приготовленного (8.3b): партия готовки, отметки, новые остатки.

Готовка отмечается по каждому блюду: блюда текущей партии (обеды и ужины,
с порциями) и сегодняшний завтрак (1 порция). Порции неотмеченного блюда
можно поменять — это меняет только списание, не план. Отметки хранит приложение —
повторная отклоняется, поэтому дважды не списать. Остатки — счётчики HA
(8.2): core отдаёт их новые значения в единицах счётчиков, HA только
выставляет.
"""

from collections.abc import Iterable, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from meal_planner.core.actions import ActionError
from meal_planner.core.models import Catalog, Recipe, Settings
from meal_planner.core.scheduling import (
    build_schedule,
    cook_weekdays,
    cook_window,
    day_zero,
    previous_cook_date,
    settle_skip,
    slot_portions,
)
from meal_planner.core.shopping import format_qty
from meal_planner.core.stock import (
    StockIgnored,
    StockLine,
    convert,
    unconvertible_reason,
)

Cooked = AbstractSet[tuple[date, str]]  # (день, recipe_id): день готовки или завтрака
# Порции, выбранные вместо плановых, — по тому же ключу (день, recipe_id).
Portions = Mapping[tuple[date, str], int]
NO_PORTIONS: Portions = {}
MIN_PORTIONS, MAX_PORTIONS = 1, 20
COUNTER_STEP = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class CookingDish:
    recipe: Recipe
    portions: int  # сколько готовят — выбранное или плановое
    planned_portions: int  # сколько дней блюдо едят по плану
    cooked: bool


@dataclass(frozen=True, slots=True)
class CookingBatch:
    cook_date: date
    dishes: tuple[CookingDish, ...]  # обеды и ужины окна, по рецепту


@dataclass(frozen=True, slots=True)
class BreakfastToday:
    date: date
    recipe: Recipe
    portions: int
    planned_portions: int  # всегда 1
    cooked: bool


@dataclass(frozen=True, slots=True)
class Cooking:
    batch: CookingBatch | None  # None — до дня 0 или готовка пропущена
    breakfast: BreakfastToday | None  # None — сегодня блюд нет


@dataclass(frozen=True, slots=True)
class CounterUpdate:
    values: tuple[tuple[str, float], ...]  # (entity_id, новое значение)
    ignored: tuple[StockIgnored, ...]  # счётчик есть, но единицу не перевести
    untracked: tuple[str, ...]  # ингредиенты блюда без счётчика


def current_cook_date(settings: Settings, today: date) -> date | None:
    """Последняя готовка не позже today; settings — после settle_skip.

    Её блюда отмечают с дня готовки до следующей: потом партия сменяется.
    """
    zero = day_zero(settings.meal_anchor_date)
    if today < zero:
        return None
    weekdays = cook_weekdays(settings)
    latest = (
        today if today.isoweekday() in weekdays else previous_cook_date(weekdays, today)
    )
    # День 0 — первая готовка, даже не в день недели готовки (6.5).
    cook_date = max(latest, zero)
    if cook_date == settings.skipped_cook_date:
        return None  # пропущенная готовка (7.7) — ничего не готовилось
    return cook_date


def batch_dishes(
    catalog: Catalog, settings: Settings, cook_date: date
) -> list[tuple[Recipe, int]]:
    """Обеды и ужины партии с порциями, по рецепту в порядке появления.

    Позиции — как «Что готовить» поездки; рецепт из двух слотов — одно блюдо.
    Рецепт — из расписания, с порцией под цель (8.5): в партии она одна.
    """
    days = build_schedule(catalog, settings, *cook_window(settings, cook_date))
    recipes = {r.id: r for d in days for r in (d.lunch, d.dinner) if r is not None}
    portions: dict[str, int] = {}
    for pair, n in slot_portions(days).items():
        for recipe_id in pair:
            portions[recipe_id] = portions.get(recipe_id, 0) + n
    return [(recipes[rid], n) for rid, n in portions.items()]


def breakfast_today(catalog: Catalog, settings: Settings, today: date) -> Recipe | None:
    (day,) = build_schedule(catalog, settings, today, today)
    return day.breakfast


def cooking(
    catalog: Catalog,
    settings: Settings,
    today: date,
    cooked: Cooked,
    portions: Portions = NO_PORTIONS,
) -> Cooking:
    settings = settle_skip(settings, today)
    cook_date = current_cook_date(settings, today)
    batch = None
    if cook_date is not None:
        batch = CookingBatch(
            cook_date=cook_date,
            dishes=tuple(
                CookingDish(
                    recipe,
                    portions.get((cook_date, recipe.id), n),
                    n,
                    (cook_date, recipe.id) in cooked,
                )
                for recipe, n in batch_dishes(catalog, settings, cook_date)
            ),
        )
    recipe = breakfast_today(catalog, settings, today)
    breakfast = (
        None
        if recipe is None
        else BreakfastToday(
            today,
            recipe,
            portions.get((today, recipe.id), 1),
            1,
            (today, recipe.id) in cooked,
        )
    )
    return Cooking(batch=batch, breakfast=breakfast)


def locate_dish(
    catalog: Catalog, settings: Settings, today: date, recipe_id: str
) -> tuple[date, Recipe, int]:
    """День отметки, рецепт (с порцией под цель, 8.5) и плановые порции
    блюда; settings — после settle_skip.

    Сегодняшний завтрак — сегодня, 1 порция; блюдо партии — день готовки.
    Иначе ActionError: блюдо сейчас не готовится.
    """
    breakfast = breakfast_today(catalog, settings, today)
    if breakfast is not None and breakfast.id == recipe_id:
        return today, breakfast, 1
    cook_date = current_cook_date(settings, today)
    if cook_date is not None:
        for recipe, n in batch_dishes(catalog, settings, cook_date):
            if recipe.id == recipe_id:
                return cook_date, recipe, n
    raise ActionError("recipe_id", "Это блюдо сейчас не готовится")


def set_portions(
    catalog: Catalog,
    settings: Settings,
    today: date,
    cooked: Cooked,
    recipe_id: str,
    portions: int,
) -> date:
    """Порции неотмеченного блюда — сколько списать при отметке; день ключа.

    План (расписание, закупка) не меняется. У отмеченного — нельзя: снятие
    отметки вернуло бы не то, что списано.
    """
    settings = settle_skip(settings, today)
    day, _, _ = locate_dish(catalog, settings, today, recipe_id)
    if (day, recipe_id) in cooked:
        raise ActionError(
            "recipe_id", "Блюдо уже отмечено — порции меняются до отметки"
        )
    if not MIN_PORTIONS <= portions <= MAX_PORTIONS:
        raise ActionError("portions", f"Порций — от {MIN_PORTIONS} до {MAX_PORTIONS}")
    return day


@dataclass(frozen=True, slots=True)
class CookedChange:
    """Что отметить или снять: ключ отметки и сколько списать."""

    day: date
    recipe: Recipe
    portions: int


def cooked_change(
    catalog: Catalog,
    settings: Settings,
    today: date,
    cooked: Cooked,
    recipe_id: str,
    mark: bool,
    portions: Portions = NO_PORTIONS,
) -> CookedChange:
    """mark — отметить, иначе снять. Невозможно — ActionError (API: 422).

    Повторная отметка отклоняется: иначе блюдо списалось бы дважды.
    Порции — выбранные (set_portions), иначе плановые.
    """
    settings = settle_skip(settings, today)
    day, recipe, planned = locate_dish(catalog, settings, today, recipe_id)
    is_cooked = (day, recipe_id) in cooked
    if mark and is_cooked:
        raise ActionError("recipe_id", "Блюдо уже отмечено как приготовленное")
    if not mark and not is_cooked:
        raise ActionError("recipe_id", "Блюдо не отмечено как приготовленное")
    return CookedChange(day, recipe, portions.get((day, recipe_id), planned))


def check_enough(
    catalog: Catalog, recipe: Recipe, portions: int, lines: Iterable[StockLine]
) -> None:
    """Хватает ли продуктов на блюдо; нет — ActionError, блюдо не отмечается.

    Проверяются ингредиенты со счётчиком, единицу которого можно перевести:
    без счётчика остаток неизвестен, и блюдо с ним иначе не отметить никогда.
    """
    by_id: dict[int, list[StockLine]] = {}
    for line in lines:
        by_id.setdefault(line.ingredient_id, []).append(line)
    missing = []
    for ing in recipe.ingredients:
        ingredient = catalog.ingredients[ing.name]
        have = None
        for line in by_id.get(ingredient.id, ()):
            per_unit = convert(ingredient, Decimal(1), line.unit)
            if per_unit is not None:
                have = (have or Decimal(0)) + Decimal(str(line.qty)) * per_unit
        need = Decimal(str(ing.qty)) * portions
        if have is not None and have < need:
            unit = ingredient.unit
            missing.append(
                f"{ingredient.name} — нужно {format_qty(float(need))} {unit},"
                f" есть {format_qty(float(round(have, 1)))} {unit}"
            )
    if missing:
        raise ActionError("stock", "Не хватает продуктов: " + "; ".join(missing))


def counter_update(
    catalog: Catalog,
    recipe: Recipe,
    portions: int,
    lines: Iterable[StockLine],
    sign: int,
) -> CounterUpdate:
    """Новые значения счётчиков: −1 — списать блюдо, +1 — вернуть.

    Списание не уводит счётчик ниже 0; на несколько счётчиков одного
    ингредиента — по порядку. Возврат — всё количество в первый счётчик:
    блюдо не готовили, продукты на месте.
    """
    # Только счётчики с сущностью HA: без неё новое значение некуда выставить.
    by_id: dict[int, list[tuple[str, StockLine]]] = {}
    for line in lines:
        if line.entity_id is not None:
            by_id.setdefault(line.ingredient_id, []).append((line.entity_id, line))
    values: list[tuple[str, float]] = []
    ignored: list[StockIgnored] = []
    untracked: list[str] = []
    for ing in recipe.ingredients:
        ingredient = catalog.ingredients[ing.name]
        counters = by_id.get(ingredient.id)
        if not counters:
            untracked.append(ingredient.name)
            continue
        need = Decimal(str(ing.qty)) * portions  # в единице ингредиента
        for entity_id, line in counters:
            per_unit = convert(ingredient, Decimal(1), line.unit)
            if per_unit is None:
                ignored.append(
                    StockIgnored(
                        line.ingredient_id,
                        ingredient.name,
                        line.qty,
                        line.unit,
                        unconvertible_reason(ingredient, line.unit),
                    )
                )
                continue
            qty = Decimal(str(line.qty))
            amount = need / per_unit  # в единице счётчика
            if sign > 0:
                new = qty + amount
            else:
                new = max(qty - amount, Decimal(0))
            new = new.quantize(COUNTER_STEP, ROUND_HALF_UP)
            values.append((entity_id, float(new)))
            need -= abs(new - qty) * per_unit
            if sign > 0 or need <= 0:
                break
    return CounterUpdate(tuple(values), tuple(ignored), tuple(untracked))
