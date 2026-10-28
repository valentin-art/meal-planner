"""Готовка и списание (8.3b, 10.1): партия, отметки, журнал, новые остатки.

«Готовка» отмечает блюда текущей партии (обеды и ужины, с порциями) и
сегодняшний завтрак (1 порция). «Рецепты» готовят любой активный рецепт
целиком или по ингредиентам: блюдо текущей партии — по ключу партии,
остальное — вне очереди, по ключу «сегодня». Отметки общие.

Порции меняются до первого списания — это меняет только списание, не план.
Журнал хранит фактически списанное по ингредиентам: отмена возвращает
ровно его, даже если при нехватке списали меньше рецепта. Повторная
готовка отклоняется, поэтому дважды не списать. Остатки — счётчики HA
(8.2): core отдаёт их новые значения в единицах счётчиков, HA только
выставляет.
"""

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from meal_planner.core.actions import ActionError
from meal_planner.core.models import (
    Catalog,
    CatalogIngredient,
    Ingredient,
    Recipe,
    Settings,
)
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

Key = tuple[date, str]  # (день, recipe_id) — ключ отметки, порций и журнала
Cooked = Collection[Key]  # отметки: день готовки партии или сегодня
CookedOn = Mapping[Key, date]  # отметка → день, когда приготовили (10.1)
# Порции, выбранные вместо плановых, — по тому же ключу.
Portions = Mapping[Key, int]
NO_PORTIONS: Portions = {}
MIN_PORTIONS, MAX_PORTIONS = 1, 20
COUNTER_STEP = Decimal("0.01")
# Журнал списаний (10.1): ingredient_id → списанное в единице ингредиента;
# None — ингредиент отмечен, списывать было не из чего.
Usage = Mapping[Key, Mapping[int, Decimal | None]]


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


def needs(
    catalog: Catalog, ingredients: Iterable[Ingredient], portions: int
) -> list[tuple[CatalogIngredient, Decimal]]:
    """Ингредиенты справочника и сколько их нужно на порции — в их единице."""
    return [
        (catalog.ingredients[ing.name], Decimal(str(ing.qty)) * portions)
        for ing in ingredients
    ]


def available(
    ingredient: CatalogIngredient, lines: Iterable[StockLine]
) -> Decimal | None:
    """Остаток по счётчикам в единице ингредиента; None — не известен."""
    have = None
    for line in lines:
        if line.ingredient_id != ingredient.id:
            continue
        per_unit = convert(ingredient, Decimal(1), line.unit)
        if per_unit is not None:
            have = (have or Decimal(0)) + Decimal(str(line.qty)) * per_unit
    return have


def shortage_line(ingredient: CatalogIngredient, need: Decimal, have: Decimal) -> str:
    unit = ingredient.unit
    return (
        f"{ingredient.name} — нужно {format_qty(float(need))} {unit},"
        f" есть {format_qty(float(round(have, 1)))} {unit}"
    )


def shortages(
    items: Iterable[tuple[CatalogIngredient, Decimal]], lines: Sequence[StockLine]
) -> list[str]:
    """Строки нехватки; без известного остатка ингредиент не проверяется."""
    missing = []
    for ingredient, need in items:
        have = available(ingredient, lines)
        if have is not None and have < need:
            missing.append(shortage_line(ingredient, need, have))
    return missing


@dataclass(frozen=True, slots=True)
class WriteOff:
    """Списание или возврат одного ингредиента."""

    values: tuple[tuple[str, float], ...]  # (entity_id, новое значение)
    ignored: tuple[StockIgnored, ...]
    # Сколько ушло из счётчиков (или вернулось) в единице ингредиента —
    # с округлением до сотых счётчика; None — счётчика, куда списать, нет.
    moved: Decimal | None


def write_off(
    ingredient: CatalogIngredient,
    amount: Decimal,
    lines: Iterable[StockLine],
    sign: int,
) -> WriteOff:
    """amount в единице ингредиента: −1 — списать до 0, +1 — вернуть в
    первый счётчик."""
    # Только счётчики с сущностью HA: без неё новое значение некуда выставить.
    values: list[tuple[str, float]] = []
    ignored: list[StockIgnored] = []
    moved = None
    for line in lines:
        if line.ingredient_id != ingredient.id or line.entity_id is None:
            continue
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
        step = amount / per_unit  # в единице счётчика
        if sign > 0:
            new = qty + step
        else:
            new = max(qty - step, Decimal(0))
        new = new.quantize(COUNTER_STEP, ROUND_HALF_UP)
        values.append((line.entity_id, float(new)))
        done = abs(new - qty) * per_unit
        moved = (moved or Decimal(0)) + done
        amount -= done
        if sign > 0 or amount <= 0:
            break
    return WriteOff(tuple(values), tuple(ignored), moved)


def move_stock(
    items: Iterable[tuple[CatalogIngredient, Decimal | None]],
    lines: Sequence[StockLine],
    sign: int,
) -> tuple[CounterUpdate, dict[int, Decimal | None]]:
    """Списать или вернуть ингредиенты; ещё — сколько ушло по ingredient_id.

    None в items — ингредиент только отмечали: счётчики не трогаются.
    """
    values: list[tuple[str, float]] = []
    ignored: list[StockIgnored] = []
    untracked: list[str] = []
    moved: dict[int, Decimal | None] = {}
    for ingredient, amount in items:
        off = None if amount is None else write_off(ingredient, amount, lines, sign)
        if off is None or not (off.values or off.ignored):
            untracked.append(ingredient.name)
        if off is not None:
            values += off.values
            ignored += off.ignored
        moved[ingredient.id] = None if off is None else off.moved
    return CounterUpdate(tuple(values), tuple(ignored), tuple(untracked)), moved


# Готовка по ингредиентам (10.1).


@dataclass(frozen=True, slots=True)
class CookState:
    """Что о готовке хранит приложение — по ключу (день, recipe_id)."""

    cooked: CookedOn
    usage: Usage
    portions: Portions


@dataclass(frozen=True, slots=True)
class CookTarget:
    """Ключ готовки рецепта: день, рецепт (с порцией под цель у партии),
    плановые порции."""

    day: date
    recipe: Recipe
    planned: int

    def key(self) -> Key:
        return self.day, self.recipe.id

    def portions(self, state: CookState) -> int:
        return state.portions.get(self.key(), self.planned)


@dataclass(frozen=True, slots=True)
class UsageChange:
    """Что записать в журнал (или вернуть из него) и новые значения счётчиков."""

    day: date
    recipe_id: str
    amounts: Mapping[int, Decimal | None]  # ingredient_id → списано/возвращено
    update: CounterUpdate


def cook_keys(
    catalog: Catalog, settings: Settings, today: date
) -> dict[str, CookTarget]:
    """Рецепты, что готовят по плану: блюда текущей партии и сегодняшний
    завтрак; settings — после settle_skip. Остальные готовят вне очереди."""
    keys: dict[str, CookTarget] = {}
    cook_date = current_cook_date(settings, today)
    if cook_date is not None:
        for recipe, n in batch_dishes(catalog, settings, cook_date):
            keys[recipe.id] = CookTarget(cook_date, recipe, n)
    breakfast = breakfast_today(catalog, settings, today)
    if breakfast is not None:
        keys.setdefault(breakfast.id, CookTarget(today, breakfast, 1))
    return keys


def resolve_key(
    catalog: Catalog, today: date, keys: Mapping[str, CookTarget], recipe_id: str
) -> CookTarget:
    """По плану — ключ из keys, иначе вне очереди: сегодня, базовый рецепт,
    1 порция. Ближайшая партия от этого не меняется."""
    if recipe_id in keys:
        return keys[recipe_id]
    recipe = catalog.recipes.get(recipe_id)
    if recipe is None:
        raise ActionError("recipe_id", "Нет такого рецепта")
    if recipe.status != "active":
        raise ActionError("recipe_id", "Рецепт не активен — его не готовят")
    return CookTarget(today, recipe, 1)


def cook_key(
    catalog: Catalog, settings: Settings, today: date, recipe_id: str
) -> CookTarget:
    """Ключ готовки любого рецепта; settings — после settle_skip."""
    return resolve_key(catalog, today, cook_keys(catalog, settings, today), recipe_id)


def _target(
    catalog: Catalog, settings: Settings, today: date, recipe_id: str, batch_only: bool
) -> CookTarget:
    settings = settle_skip(settings, today)
    if batch_only:  # «Готовка» (8.3b): только блюда партии и завтрак
        return CookTarget(*locate_dish(catalog, settings, today, recipe_id))
    return cook_key(catalog, settings, today, recipe_id)


def _usage_change(
    target: CookTarget,
    items: Iterable[tuple[CatalogIngredient, Decimal | None]],
    lines: Iterable[StockLine],
    sign: int,
) -> UsageChange:
    update, moved = move_stock(items, list(lines), sign)
    return UsageChange(target.day, target.recipe.id, moved, update)


def _not_cooked(state: CookState, target: CookTarget) -> None:
    if target.key() in state.cooked:
        raise ActionError("recipe_id", "Блюдо уже отмечено как приготовленное")


def use_ingredient(
    catalog: Catalog,
    settings: Settings,
    today: date,
    state: CookState,
    recipe_id: str,
    ingredient_id: int,
    lines: Iterable[StockLine],
    batch_only: bool = False,
) -> UsageChange:
    """Списать один ингредиент на порции рецепта — сколько есть, до 0.

    Без подтверждения: нехватку видно на плитке заранее.
    """
    target = _target(catalog, settings, today, recipe_id, batch_only)
    _not_cooked(state, target)
    if ingredient_id in state.usage.get(target.key(), {}):
        raise ActionError("ingredient_id", "Ингредиент уже использован")
    items = needs(catalog, target.recipe.ingredients, target.portions(state))
    item = [(i, need) for i, need in items if i.id == ingredient_id]
    if not item:
        raise ActionError("ingredient_id", "Этого ингредиента нет в рецепте")
    return _usage_change(target, item, lines, -1)


def unuse_ingredient(
    catalog: Catalog,
    settings: Settings,
    today: date,
    state: CookState,
    recipe_id: str,
    ingredient_id: int,
    lines: Iterable[StockLine],
    batch_only: bool = False,
) -> UsageChange:
    """Вернуть ровно списанное по ингредиенту."""
    target = _target(catalog, settings, today, recipe_id, batch_only)
    if target.key() in state.cooked:
        raise ActionError("recipe_id", "Блюдо приготовлено — сначала снимите отметку")
    used = state.usage.get(target.key(), {})
    if ingredient_id not in used:
        raise ActionError("ingredient_id", "Ингредиент не использован")
    ingredient = _by_id(catalog)[ingredient_id]
    return _usage_change(target, [(ingredient, used[ingredient_id])], lines, 1)


def cook_recipe(
    catalog: Catalog,
    settings: Settings,
    today: date,
    state: CookState,
    recipe_id: str,
    lines: Iterable[StockLine],
    allow_missing: bool = False,
    batch_only: bool = False,
) -> UsageChange:
    """Списать неиспользованные ингредиенты; отметку (cooked_on = today)
    ставит вызывающий.

    Не хватает — ActionError со списком, если нехватку не подтвердили
    (allow_missing); с подтверждением списывается сколько есть, до 0.
    """
    target = _target(catalog, settings, today, recipe_id, batch_only)
    _not_cooked(state, target)
    used = state.usage.get(target.key(), {})
    rest = [
        (i, need)
        for i, need in needs(catalog, target.recipe.ingredients, target.portions(state))
        if i.id not in used
    ]
    lines = list(lines)
    if not allow_missing:
        missing = shortages(rest, lines)
        if missing:
            raise ActionError("stock", "Не хватает продуктов: " + "; ".join(missing))
    return _usage_change(target, rest, lines, -1)


def uncook_recipe(
    catalog: Catalog,
    settings: Settings,
    today: date,
    state: CookState,
    recipe_id: str,
    lines: Iterable[StockLine],
    batch_only: bool = False,
) -> UsageChange:
    """Вернуть всё, что записано в журнал по рецепту; отметку снимает
    вызывающий. Отметка 8.3b без журнала — возврат всего по рецепту."""
    target = _target(catalog, settings, today, recipe_id, batch_only)
    if target.key() not in state.cooked:
        raise ActionError("recipe_id", "Блюдо не отмечено как приготовленное")
    used = state.usage.get(target.key())
    if used:
        by_id = _by_id(catalog)
        items = [(by_id[i], amount) for i, amount in used.items()]
    else:
        items = needs(catalog, target.recipe.ingredients, target.portions(state))
    return _usage_change(target, items, lines, 1)


def check_portions(state: CookState, key: Key, portions: int) -> None:
    """Порции меняются до первого списания: иначе отмена вернула бы не то."""
    if key in state.cooked:
        raise ActionError(
            "recipe_id", "Блюдо уже отмечено — порции меняются до отметки"
        )
    if state.usage.get(key):
        raise ActionError(
            "recipe_id", "Продукты уже списаны — порции меняются до первого списания"
        )
    if not MIN_PORTIONS <= portions <= MAX_PORTIONS:
        raise ActionError("portions", f"Порций — от {MIN_PORTIONS} до {MAX_PORTIONS}")


def set_recipe_portions(
    catalog: Catalog,
    settings: Settings,
    today: date,
    state: CookState,
    recipe_id: str,
    portions: int,
    batch_only: bool = False,
) -> date:
    """Порции рецепта — сколько списывать; день ключа. План (расписание,
    закупка) не меняется."""
    target = _target(catalog, settings, today, recipe_id, batch_only)
    check_portions(state, target.key(), portions)
    return target.day


def _by_id(catalog: Catalog) -> dict[int, CatalogIngredient]:
    return {i.id: i for i in catalog.ingredients.values()}
