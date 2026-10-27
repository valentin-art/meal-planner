"""Рецепты в том виде, как они были в meal-plan.jsx (golden_recipes.json).

С 6.7 данные в recipes_data.py другие (граммы вместо долей штуки), но
алгоритм агрегации по-прежнему сверяется с JS — на замороженных JSX-данных.
"""

import json
from dataclasses import replace
from pathlib import Path

from meal_planner.core.models import (
    Catalog,
    CatalogIngredient,
    Ingredient,
    MealKind,
    Recipe,
)
from meal_planner.core.seed import SEED_CATALOG

GOLDEN = json.loads((Path(__file__).parent / "golden_recipes.json").read_text("utf-8"))
JS_KIND = {
    "завтрак": MealKind.BREAKFAST,
    "обед": MealKind.LUNCH,
    "ужин": MealKind.DINNER,
}

JSX_RECIPES = {
    rid: Recipe(
        id=rid,
        name=js["name"],
        kind=JS_KIND[js["kind"]],
        kcal=js["kcal"],
        protein=js["protein"],
        ingredients=tuple(
            Ingredient(i["name"], i["qty"], i["unit"], i["cat"])
            for i in js["ingredients"]
        ),
        steps=tuple(js["steps"]),
        is_fish=rid in GOLDEN["fish_ids"],
    )
    for rid, js in GOLDEN["recipes"].items()
}

# Каталог для сверки агрегации с JS (5.3): названия ингредиентов из JSX, без
# «≈ N шт» — в JSX этой подсказки не было. У «цукини» в JSX две единицы (шт и
# г), в справочнике остаётся одна — агрегация берёт единицу из строки рецепта.
JSX_CATALOG = Catalog(
    recipes=JSX_RECIPES,
    ingredients={
        i.name: CatalogIngredient(0, i.name, i.unit, i.category, 0, 0, None, None, None)
        for r in JSX_RECIPES.values()
        for i in r.ingredients
    },
    slots=SEED_CATALOG.slots,
)


def as_jsx(day):
    """День расписания с теми же блюдами, но JSX-рецептами."""
    return replace(
        day,
        **{
            slot: JSX_RECIPES[recipe.id]
            for slot in ("breakfast", "lunch", "dinner")
            if (recipe := getattr(day, slot)) is not None
        },
    )
