import json
from pathlib import Path

import pytest

from meal_planner.core.models import MealKind
from meal_planner.core.recipes_data import ALL_RECIPES

GOLDEN = json.loads((Path(__file__).parent / "golden_recipes.json").read_text("utf-8"))
JS_KIND = {
    "завтрак": MealKind.BREAKFAST,
    "обед": MealKind.LUNCH,
    "ужин": MealKind.DINNER,
}


def test_ids_and_order_match_jsx():
    assert list(ALL_RECIPES) == list(GOLDEN["recipes"])
    assert all(recipe.id == rid for rid, recipe in ALL_RECIPES.items())


@pytest.mark.parametrize("rid", list(GOLDEN["recipes"]))
def test_recipe_matches_jsx(rid):
    js = GOLDEN["recipes"][rid]
    recipe = ALL_RECIPES[rid]
    assert recipe.name == js["name"]
    assert recipe.kind is JS_KIND[js["kind"]]
    assert recipe.kcal == js["kcal"]
    assert recipe.protein == js["protein"]
    assert [(i.name, i.qty, i.unit, i.category) for i in recipe.ingredients] == [
        (i["name"], i["qty"], i["unit"], i["cat"]) for i in js["ingredients"]
    ]
    assert list(recipe.steps) == js["steps"]
    if "storage" in js:
        storage = recipe.storage
        assert storage is not None
        assert (storage.keep, storage.pan, storage.micro, storage.after) == (
            js["storage"]["keep"],
            js["storage"]["pan"],
            js["storage"]["micro"],
            js["storage"].get("after"),
        )
    else:
        assert recipe.storage is None


def test_is_fish_matches_fish_ids():
    assert {rid for rid, r in ALL_RECIPES.items() if r.is_fish} == set(
        GOLDEN["fish_ids"]
    )


def test_catalog_is_read_only():
    with pytest.raises(TypeError):
        ALL_RECIPES["b1"] = ALL_RECIPES["b2"]
