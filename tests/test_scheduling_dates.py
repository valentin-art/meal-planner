import dataclasses
from datetime import date

import pytest
from golden_dates import COOK_DATE_CASES, FISH_PRIORITY
from golden_scheduling import SCENARIOS
from scenario_settings import settings_of

from meal_planner.core.scheduling import (
    breakfast_for_date,
    cook_weekdays,
    dish_index_for_date,
    fish_priority,
    nearest_cook_date,
    next_date_for_weekday,
    previous_cook_date,
)
from meal_planner.core.seed import SEED_CATALOG


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["name"])
def test_dish_index_and_breakfast_match_golden(scenario):
    anchor = settings_of(scenario).meal_anchor_date
    for case in scenario["cases"]:
        d = date.fromisoformat(case["date"])
        assert dish_index_for_date(d, anchor) == case["natural_cycle_index"], case
        assert breakfast_for_date(d, anchor) == case["breakfast"], case


def test_fish_priority_matches_golden():
    assert {k: fish_priority(SEED_CATALOG, k) for k in range(1, 6)} == FISH_PRIORITY


@pytest.mark.parametrize(
    "case", COOK_DATE_CASES, ids=lambda c: f"{c['cook_weekdays']}-{c['from']}"
)
def test_cook_dates_match_golden(case):
    weekdays = tuple(case["cook_weekdays"])
    from_date = date.fromisoformat(case["from"])
    assert nearest_cook_date(weekdays, from_date).isoformat() == case["nearest"]
    assert previous_cook_date(weekdays, from_date).isoformat() == case["previous"]
    if "next_for_weekday" in case:
        next_date = next_date_for_weekday(weekdays[0], from_date)
        assert next_date.isoformat() == case["next_for_weekday"]


def test_cook_weekdays_ignores_second_day_for_single_cook():
    by_name = {s["name"]: settings_of(s) for s in SCENARIOS}
    assert cook_weekdays(by_name["single_1x_sunday"]) == (7,)
    assert cook_weekdays(by_name["default_2x"]) == (3, 6)


def test_cook_weekdays_requires_second_day_for_double_cook():
    settings = dataclasses.replace(
        settings_of(SCENARIOS[0]), cook_count=2, cook_weekday2=None
    )
    with pytest.raises(ValueError):
        cook_weekdays(settings)
