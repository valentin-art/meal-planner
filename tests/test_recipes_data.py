import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from jsx_recipes import GOLDEN, JS_KIND

from meal_planner.core.models import MealKind
from meal_planner.core.recipes_data import ALL_RECIPES, STORE_PIECES
from meal_planner.core.scheduling import breakfast_for_date

ROOT = Path(__file__).parents[1]
SNAPSHOT = Path(__file__).parent / "recipes_snapshot.json"


# 5.1a: b1 из JSX разделён на глазунью (b1) и бенедикт (b3, только в пуле).
SPLIT_FROM_JSX = {"b1"}
NOT_IN_JSX = {"b3"}


def test_ids_and_order_match_jsx():
    assert [rid for rid in ALL_RECIPES if rid not in NOT_IN_JSX] == list(
        GOLDEN["recipes"]
    )
    assert all(recipe.id == rid for rid, recipe in ALL_RECIPES.items())


@pytest.mark.parametrize("rid", sorted(set(GOLDEN["recipes"]) - SPLIT_FROM_JSX))
def test_recipe_matches_jsx(rid):
    # С 6.7 количества, единицы и тексты с ними — свои, с 6.9 ккал/белок
    # считаются (см. снапшот ниже); с JSX сверяется то, что не менялось.
    js = GOLDEN["recipes"][rid]
    recipe = ALL_RECIPES[rid]
    assert recipe.name == js["name"]
    assert recipe.kind is JS_KIND[js["kind"]]
    assert len(recipe.steps) == len(js["steps"])
    assert ("storage" in js) == (recipe.storage is not None)
    if recipe.storage is not None:
        assert (recipe.storage.keep, recipe.storage.pan, recipe.storage.micro) == (
            js["storage"]["keep"],
            js["storage"]["pan"],
            js["storage"]["micro"],
        )


def load_generator():
    spec = importlib.util.spec_from_file_location(
        "dump_recipes", ROOT / "tools" / "dump_recipes.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_recipes_snapshot_is_current():
    assert load_generator().build() == json.loads(SNAPSHOT.read_text("utf-8")), (
        "Рецепты изменились: uv run python tools/dump_recipes.py"
        " > tests/recipes_snapshot.json"
    )


def test_store_pieces_refer_to_used_ingredients():
    used = {(i.name, i.unit) for r in ALL_RECIPES.values() for i in r.ingredients}
    assert set(STORE_PIECES) <= used


def test_is_fish_matches_fish_ids():
    assert {rid for rid, r in ALL_RECIPES.items() if r.is_fish} == set(
        GOLDEN["fish_ids"]
    )


def test_benedict_is_split_from_b1_and_not_scheduled():
    b1, b3 = ALL_RECIPES["b1"], ALL_RECIPES["b3"]
    assert b3.kind is MealKind.BREAKFAST
    assert not any("пашот" in step or "бенедикт" in step for step in b1.steps)
    # Ингредиенты b1 прежние — ккал и golden расписания не меняются.
    assert [(i.name, i.qty, i.unit) for i in b1.ingredients] == [
        ("яйца", 4, "шт"),
        ("масло сливочное", 1, "ст.л."),
        ("хлеб цельнозерновой", 60, "г"),
    ]
    anchor = date(2026, 11, 2)
    breakfasts = {
        breakfast_for_date(anchor + timedelta(days=i), anchor) for i in range(10)
    }
    assert breakfasts == {"b1", "b2"}


def test_catalog_is_read_only():
    with pytest.raises(TypeError):
        ALL_RECIPES["b1"] = ALL_RECIPES["b2"]
