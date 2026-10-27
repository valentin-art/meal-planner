"""6.9: ккал и белок рецепта — сумма по ингредиентам (core/nutrition.py)."""

import pytest

from meal_planner.core.models import Ingredient
from meal_planner.core.nutrition import GRAMS_PER_UNIT, PER_100G, nutrition
from meal_planner.core.seed import SEED_CATALOG

INGREDIENTS = {
    (i.name, i.unit) for r in SEED_CATALOG.recipes.values() for i in r.ingredients
}


def test_every_ingredient_has_nutrition():
    assert {name for name, _ in INGREDIENTS} == set(PER_100G)


def test_every_non_gram_unit_has_weight():
    assert {k for k in INGREDIENTS if k[1] not in ("г", "мл")} == set(GRAMS_PER_UNIT)


def test_recipe_is_sum_of_ingredients():
    # b1: яйца 4 × 50 г, масло 1 ст.л. = 14.2 г, хлеб 60 г.
    kcal = 200 * 1.43 + 14.2 * 7.17 + 60 * 2.52  # 539.0
    protein = 200 * 0.1256 + 14.2 * 0.0085 + 60 * 0.1245  # 32.7
    assert SEED_CATALOG.recipes["b1"].kcal == 540 == round(kcal / 5) * 5
    assert SEED_CATALOG.recipes["b1"].protein == 33 == round(protein)


@pytest.mark.parametrize(
    ("name", "grams", "expected"),
    [
        ("куриное филе", 100, (120, 23)),  # белок 22.5 → 23: половина вверх
        ("куриное филе", 50, (60, 11)),  # 11.25 → 11
        ("цукини", 100, (15, 1)),  # 17 ккал → до 5: 15
        ("огурец", 150, (25, 1)),  # 22.5 ккал = 4.5 × 5 → 25
    ],
)
def test_rounding_kcal_to_5_protein_to_1(name, grams, expected):
    assert (
        nutrition(SEED_CATALOG.ingredients, [Ingredient(name, grams, "г", "Овощи")])
        == expected
    )
