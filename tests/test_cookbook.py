"""9.1a: ближайшая готовка и книга рецептов для дашборда «Кухня»."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from meal_planner.core.catalog import build_catalog
from meal_planner.core.cookbook import (
    cookbook_recipes,
    upcoming_cook,
    upcoming_cook_date,
)
from meal_planner.core.cooking import cooking
from meal_planner.core.models import MealKind, PortionScale, Settings
from meal_planner.core.portions import scaled
from meal_planner.core.recipes_data import RECIPE_SPECS
from meal_planner.core.scheduling import trip_cook_date
from meal_planner.core.seed import SEED_CATALOG, seed_ingredients, seed_slot_versions

# Готовка Ср/Сб; якорь Чт 05.11 — день 0 Ср 04.11 (совпал с днём готовки).
SETTINGS = Settings(date(2026, 11, 5), 2, 3, 6, "same")
SKIPPED = replace(SETTINGS, skipped_cook_date=date(2026, 11, 11))


def ids(recipes) -> list[str]:
    return [r.id for r in recipes]


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 11, 1), date(2026, 11, 4)),  # до старта — день 0
        (date(2026, 11, 4), date(2026, 11, 4)),  # день 0
        (date(2026, 11, 5), date(2026, 11, 7)),
        (date(2026, 11, 7), date(2026, 11, 7)),  # день готовки
        (date(2026, 11, 8), date(2026, 11, 11)),
    ],
)
def test_upcoming_cook_date(today, expected):
    assert upcoming_cook_date(SETTINGS, today) == expected


def test_day_zero_not_on_cook_weekday():
    # Якорь Пн 09.11: день 0 — Вс 08.11, не день готовки (6.5).
    s = replace(SETTINGS, meal_anchor_date=date(2026, 11, 9))
    assert upcoming_cook_date(s, date(2026, 11, 6)) == date(2026, 11, 8)
    assert upcoming_cook_date(s, date(2026, 11, 9)) == date(2026, 11, 11)


@pytest.mark.parametrize("day", [10, 11, 12, 14])
def test_skipped_cook_moves_to_pause_end(day):
    # Готовка Ср 11.11 пропущена (7.7): пауза Чт–Сб, в Сб снова готовка.
    assert upcoming_cook_date(SKIPPED, date(2026, 11, day)) == date(2026, 11, 14)


def test_past_pause_settles():
    assert upcoming_cook(SEED_CATALOG, SKIPPED, date(2026, 11, 15)).cook_date == date(
        2026, 11, 18
    )


@pytest.mark.parametrize("timing", ["same", "before"])
def test_shopping_timing_does_not_matter(timing):
    s = replace(SETTINGS, shopping_timing=timing)
    assert upcoming_cook_date(s, date(2026, 11, 10)) == date(2026, 11, 11)
    assert upcoming_cook_date(s, date(2026, 11, 11)) == date(2026, 11, 11)


def test_unlike_the_trip_cook_date():
    # Закупка «за день» в день готовки уже к следующей — готовка сегодня.
    s = replace(SETTINGS, shopping_timing="before")
    assert trip_cook_date(s, date(2026, 11, 11)) == date(2026, 11, 14)
    assert upcoming_cook_date(s, date(2026, 11, 11)) == date(2026, 11, 11)


@pytest.mark.parametrize("day", [10, 11])
def test_dishes_are_the_batch_of_that_cook(day):
    state = cooking(SEED_CATALOG, SETTINGS, date(2026, 11, 11), set())
    assert state.batch is not None
    expected = [(d.recipe, d.planned_portions) for d in state.batch.dishes]
    cook = upcoming_cook(SEED_CATALOG, SETTINGS, date(2026, 11, day))
    assert cook.cook_date == date(2026, 11, 11)
    assert [(d.recipe, d.planned_portions) for d in cook.dishes] == expected


def test_dishes_have_the_target_portion():
    s = replace(
        SETTINGS,
        daily_kcal_target=2700,
        portion_scales=(PortionScale(date(2026, 11, 5), 2700),),
    )
    dishes = upcoming_cook(SEED_CATALOG, s, date(2026, 11, 10)).dishes
    scaled_dishes = [d for d in dishes if d.recipe.scale != 1]
    assert scaled_dishes
    for d in scaled_dishes:
        base = SEED_CATALOG.recipes[d.recipe.id]
        factor = Decimal(str(d.recipe.scale))
        assert d.recipe.ingredients == scaled(SEED_CATALOG, base, factor).ingredients


def test_cookbook_has_all_seed_recipes_by_kind():
    book = cookbook_recipes(SEED_CATALOG)
    assert ids(book[MealKind.BREAKFAST]) == ["b1", "b2", "b3"]
    assert ids(book[MealKind.LUNCH]) == [f"l{i}" for i in range(1, 6)]
    assert ids(book[MealKind.DINNER]) == [f"d{i}" for i in range(1, 6)]


def test_cookbook_skips_drafts_and_archive_keeps_number_order():
    specs = [
        *RECIPE_SPECS.values(),
        replace(RECIPE_SPECS["l1"], id="l10"),
        replace(RECIPE_SPECS["l1"], id="l6", status="draft"),
        replace(RECIPE_SPECS["d1"], id="d6", status="archived"),
    ]
    book = cookbook_recipes(
        build_catalog(seed_ingredients(), specs, seed_slot_versions())
    )
    assert ids(book[MealKind.LUNCH]) == [*(f"l{i}" for i in range(1, 6)), "l10"]
    assert ids(book[MealKind.DINNER]) == [f"d{i}" for i in range(1, 6)]
