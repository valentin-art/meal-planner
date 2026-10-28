"""Книга рецептов для дашборда «Кухня» (9.1): ближайшая готовка и активные
рецепты по видам; готовка по ингредиентам (10.1) — что списано и чего не
хватает.

Дашборд только раскладывает: какие блюда готовить, с какой порцией и что
показывать в группах — решает core.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from types import MappingProxyType

from meal_planner.core.cooking import (
    CookState,
    available,
    batch_dishes,
    cook_keys,
    needs,
    resolve_key,
    shortage_line,
)
from meal_planner.core.models import Catalog, MealKind, Recipe, Settings
from meal_planner.core.scheduling import (
    cook_date_for_trip,
    cook_weekdays,
    pause_end,
    settle_skip,
)
from meal_planner.core.stock import StockLine


@dataclass(frozen=True, slots=True)
class UpcomingDish:
    recipe: Recipe  # с порцией под цель (8.5): количества и ккал уже умножены
    planned_portions: int  # сколько дней блюдо едят по плану


@dataclass(frozen=True, slots=True)
class UpcomingCook:
    cook_date: date
    dishes: tuple[UpcomingDish, ...]  # обеды и ужины, по рецепту в порядке появления


def upcoming_cook_date(settings: Settings, today: date) -> date:
    """Ближайшая готовка не раньше today; settings — после settle_skip.

    До старта — день 0; пропущенная (7.7) — конец паузы. В отличие от
    trip_cook_date, без поправки shopping_timing="before": нужна готовка, а не
    закупка. В день готовки — сегодня, даже если всё уже отмечено.
    """
    weekdays = cook_weekdays(settings)
    cook_date = cook_date_for_trip(settings.meal_anchor_date, weekdays, today)
    if cook_date == settings.skipped_cook_date:
        cook_date = pause_end(cook_date, weekdays)
    return cook_date


def upcoming_cook(catalog: Catalog, settings: Settings, today: date) -> UpcomingCook:
    settings = settle_skip(settings, today)
    cook_date = upcoming_cook_date(settings, today)
    return UpcomingCook(
        cook_date,
        tuple(
            UpcomingDish(r, n) for r, n in batch_dishes(catalog, settings, cook_date)
        ),
    )


def cookbook_recipes(catalog: Catalog) -> Mapping[MealKind, tuple[Recipe, ...]]:
    """Активные рецепты по видам — без черновиков (8.6a) и архива (5.1).

    Порядок — каталога: по номеру id (catalog_order), l2 раньше l10.
    """
    active = [r for r in catalog.recipes.values() if r.status == "active"]
    return MappingProxyType(
        {kind: tuple(r for r in active if r.kind == kind) for kind in MealKind}
    )


@dataclass(frozen=True, slots=True)
class IngredientCooking:
    ingredient_id: int
    name: str
    unit: str
    need: Decimal  # на порции рецепта, в единице ингредиента
    have: Decimal | None  # остаток по счётчикам; None — не известен
    used: bool  # уже списан (или отмечен) по журналу
    short: bool  # не хватает — только у неиспользованных и до готовки


@dataclass(frozen=True, slots=True)
class RecipeCooking:
    day: date  # день ключа: готовка партии или сегодня
    portions: int  # выбранные или плановые
    planned_portions: int
    locked: bool  # порции не поменять: уже списано или приготовлено
    cooked: bool
    cooked_today: bool  # серый на «Рецептах» — до конца дня действия
    ingredients: tuple[IngredientCooking, ...]
    missing: tuple[str, ...]  # строки нехватки для подтверждения


def cook_preview(
    catalog: Catalog,
    settings: Settings,
    today: date,
    state: CookState,
    lines: Iterable[StockLine],
) -> Mapping[str, RecipeCooking]:
    """Готовка каждого рецепта книги по recipe_id: активные и блюда
    ближайшей партии.

    Одно состояние на рецепт: у плитки ряда «Ближайшая готовка» и группы
    оно общее. Блюдо ближайшей партии вне текущей готовят вне очереди.
    """
    settings = settle_skip(settings, today)
    keys = cook_keys(catalog, settings, today)
    lines = list(lines)
    ids = [r.id for rs in cookbook_recipes(catalog).values() for r in rs]
    ids += [d.recipe.id for d in upcoming_cook(catalog, settings, today).dishes]
    ids += keys
    preview: dict[str, RecipeCooking] = {}
    for recipe_id in dict.fromkeys(ids):
        recipe = catalog.recipes.get(recipe_id)
        if recipe_id not in keys and (recipe is None or recipe.status != "active"):
            continue  # не в плане и не активен — не готовится
        target = resolve_key(catalog, today, keys, recipe_id)
        key = target.key()
        used = state.usage.get(key, {})
        cooked_on = state.cooked.get(key)
        portions = target.portions(state)
        ingredients = []
        missing = []
        for ingredient, need in needs(catalog, target.recipe.ingredients, portions):
            have = available(ingredient, lines)
            is_used = ingredient.id in used
            short = False
            if have is not None and have < need and cooked_on is None and not is_used:
                short = True
                missing.append(shortage_line(ingredient, need, have))
            ingredients.append(
                IngredientCooking(
                    ingredient.id,
                    ingredient.name,
                    ingredient.unit,
                    need,
                    have,
                    is_used,
                    short,
                )
            )
        preview[recipe_id] = RecipeCooking(
            target.day,
            portions,
            target.planned,
            cooked_on is not None or bool(used),
            cooked_on is not None,
            cooked_on == today,
            tuple(ingredients),
            tuple(missing),
        )
    return MappingProxyType(preview)
