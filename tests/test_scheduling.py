from datetime import date

import pytest
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


def as_case(day) -> dict:
    """DayMeals → поля golden-случая (без tags и natural_cycle_index)."""
    return {
        "date": day.date.isoformat(),
        "cycle_index": day.cycle_index,
        "breakfast": day.breakfast.id,
        "lunch": day.lunch.id,
        "dinner": day.dinner.id,
        "is_day_zero": day.is_day_zero,
        "is_cook_day": day.is_cook_day,
        "kcal_total": day.kcal_total,
        "protein_total": day.protein_total,
    }


def expected(case) -> dict:
    return {k: v for k, v in case.items() if k not in ("tags", "natural_cycle_index")}


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["name"])
def test_meals_for_date_matches_golden(scenario):
    settings = settings_of(scenario)
    weekdays = cook_weekdays(settings)
    for case in scenario["cases"]:
        day = meals_for_date(
            date.fromisoformat(case["date"]), settings.meal_anchor_date, weekdays
        )
        assert as_case(day) == expected(case), case["tags"]


@pytest.mark.parametrize("scenario", SWEEPS, ids=lambda s: s["name"])
def test_build_schedule_matches_golden(scenario):
    cases = scenario["cases"]
    schedule = build_schedule(
        settings_of(scenario),
        date.fromisoformat(cases[0]["date"]),
        date.fromisoformat(cases[-1]["date"]),
    )
    assert [as_case(day) for day in schedule] == [expected(c) for c in cases]


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
