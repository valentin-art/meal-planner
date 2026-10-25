import dataclasses
from datetime import date

import pytest
from golden_scheduling import SCENARIOS

from meal_planner.core.models import (
    Ingredient,
    MealKind,
    Recipe,
    Settings,
    default_settings,
)


def make_recipe() -> Recipe:
    return Recipe(
        id="l1",
        name="Курица с киноа и овощами",
        kind=MealKind.LUNCH,
        kcal=780,
        protein=66,
        ingredients=(Ingredient("куриное филе", 180, "г", "Белок"),),
        steps=("Промойте киноа.",),
    )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["name"])
def test_golden_settings_map_onto_settings(scenario):
    raw = scenario["settings"]
    settings = Settings(
        **{**raw, "meal_anchor_date": date.fromisoformat(raw["meal_anchor_date"])}
    )
    assert settings.cook_weekday1 == raw["cook_weekday1"]


def test_models_are_frozen():
    recipe = make_recipe()
    with pytest.raises(dataclasses.FrozenInstanceError):
        recipe.kcal = 0


def test_meal_kind_is_str():
    assert MealKind("lunch") is MealKind.LUNCH
    assert MealKind.LUNCH == "lunch"


def test_recipe_defaults_and_hashable():
    recipe = make_recipe()
    assert recipe.storage is None
    assert recipe.is_fish is False
    assert {recipe: 1}[make_recipe()] == 1


def test_default_settings_match_js():
    # JS DEFAULT_SETTINGS: якорь — завтра, 2 готовки Ср/Сб, закупка в день готовки
    assert default_settings(date(2026, 11, 9)) == Settings(
        date(2026, 11, 10), 2, 3, 6, "same"
    )
