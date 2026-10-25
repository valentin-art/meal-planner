from datetime import date

import pytest
from cycle_start import CLIPPED_DAYS, before_anchor
from golden_scheduling import SCENARIOS
from scenario_settings import settings_of

from meal_planner.core.recipes_data import ALL_RECIPES
from meal_planner.core.scheduling import (
    build_schedule,
    cook_weekdays,
    day_totals,
    meals_for_date,
)

SWEEPS = [s for s in SCENARIOS if all("sweep" in c["tags"] for c in s["cases"])]


def recipe_id(recipe):
    return recipe.id if recipe else None


def as_case(day) -> dict:
    """DayMeals → поля golden-случая (без tags и natural_cycle_index)."""
    return {
        "date": day.date.isoformat(),
        "cycle_index": day.cycle_index,
        "breakfast": recipe_id(day.breakfast),
        "lunch": recipe_id(day.lunch),
        "dinner": recipe_id(day.dinner),
        "is_day_zero": day.is_day_zero,
        "is_cook_day": day.is_cook_day,
        "is_before_start": day.is_before_start,
        "kcal_total": day.kcal_total,
        "protein_total": day.protein_total,
    }


def expected(scenario, case) -> dict:
    """Golden JSX, кроме случаев, которые меняет правило 6.5 (cycle_start.py)."""
    d = date.fromisoformat(case["date"])
    anchor = settings_of(scenario).meal_anchor_date
    if d < anchor:
        return before_anchor(d, anchor)
    jsx = {k: v for k, v in case.items() if k not in ("tags", "natural_cycle_index")}
    return {
        **jsx,
        "is_before_start": False,
        **CLIPPED_DAYS.get((scenario["name"], case["date"]), {}),
    }


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["name"])
def test_meals_for_date_matches_golden(scenario):
    settings = settings_of(scenario)
    weekdays = cook_weekdays(settings)
    for case in scenario["cases"]:
        day = meals_for_date(
            date.fromisoformat(case["date"]), settings.meal_anchor_date, weekdays
        )
        assert as_case(day) == expected(scenario, case), case["tags"]


@pytest.mark.parametrize("scenario", SWEEPS, ids=lambda s: s["name"])
def test_build_schedule_matches_golden(scenario):
    cases = scenario["cases"]
    schedule = build_schedule(
        settings_of(scenario),
        date.fromisoformat(cases[0]["date"]),
        date.fromisoformat(cases[-1]["date"]),
    )
    assert [as_case(day) for day in schedule] == [expected(scenario, c) for c in cases]


def test_build_schedule_bounds():
    settings = settings_of(SCENARIOS[0])
    d = date(2026, 11, 9)
    assert [day.date for day in build_schedule(settings, d, d)] == [d]
    assert build_schedule(settings, d, date(2026, 11, 8)) == ()


def test_day_totals():
    # default_2x, 2026-11-07: b1 + l1 + d1
    assert day_totals(ALL_RECIPES["b1"], ALL_RECIPES["l1"], ALL_RECIPES["d1"]) == (
        2025,
        151,
    )
