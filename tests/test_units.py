"""Перевод количества в строке рецепта в единицу справочника (8.6b)."""

from dataclasses import replace
from decimal import Decimal

import pytest

from meal_planner.core.models import CatalogIngredient
from meal_planner.core.units import rounded, to_ingredient_unit

GRAMS = CatalogIngredient(1, "морковь", "г", "Овощи", 41, 0.93, None, None, 80)
ML = replace(GRAMS, name="молоко", unit="мл", store_piece=None)
TSP = replace(GRAMS, name="паприка", unit="ч.л.", grams_per_unit=2.3, store_piece=None)
TBSP = replace(TSP, name="масло", unit="ст.л.", grams_per_unit=13.6)
CLOVE = replace(GRAMS, name="чеснок", unit="зубчик", grams_per_unit=3, store_piece=None)


@pytest.mark.parametrize(
    ("ingredient", "qty", "unit", "expected"),
    [
        (GRAMS, "0.5", "кг", "500"),
        (ML, "0.3", "л", "300"),
        (ML, "1", "стакан", "250"),
        (ML, "2", "ст.л.", "30"),
        (GRAMS, "120", "мл", "120"),  # г и мл — 1:1
        (ML, "50", "г", "50"),
        (GRAMS, "2", "шт", "160"),  # вес штуки в магазине
        (TSP, "1", "ст.л.", "3"),
        (TBSP, "3", "ч.л.", "1"),
        (TBSP, "30", "мл", "2"),
        (TSP, "4.6", "г", "2"),
        (CLOVE, "9", "г", "3"),
        (CLOVE, "2", "зубчик", "2"),
    ],
)
def test_converts(ingredient, qty, unit, expected):
    assert to_ingredient_unit(ingredient, Decimal(qty), unit) == Decimal(expected)


@pytest.mark.parametrize(
    ("ingredient", "unit"),
    [
        (GRAMS, "ч.л."),  # вес ложки у ингредиента в г неизвестен
        (ML, "шт"),  # нет веса штуки
        (GRAMS, "зубчик"),
        (TSP, "шт"),
        (GRAMS, "щепотка"),
        (replace(TSP, grams_per_unit=None), "г"),
    ],
)
def test_unconvertible(ingredient, unit):
    assert to_ingredient_unit(ingredient, Decimal(1), unit) is None


@pytest.mark.parametrize(
    ("unit", "qty", "expected"),
    [
        ("г", "180.5", "181"),
        ("мл", "12.4", "12"),
        ("ч.л.", "0.33", "0.25"),
        ("ч.л.", "0.375", "0.5"),  # половина вверх
        ("ст.л.", "1.6", "1.5"),
        ("шт", "0.1", "0"),  # ноль отклонит save_recipe
    ],
)
def test_rounding(unit, qty, expected):
    assert rounded(unit, Decimal(qty)) == Decimal(expected)
