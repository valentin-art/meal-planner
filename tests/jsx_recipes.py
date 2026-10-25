"""Рецепты в том виде, как они были в meal-plan.jsx (golden_recipes.json).

С 6.7 данные в recipes_data.py другие (граммы вместо долей штуки), но
алгоритм агрегации по-прежнему сверяется с JS — на замороженных JSX-данных.
"""

import json
from dataclasses import replace
from pathlib import Path

from meal_planner.core.models import Ingredient, MealKind, Recipe

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
