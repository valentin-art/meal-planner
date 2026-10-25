from datetime import date, timedelta

import pytest
from golden_scheduling import SCENARIOS
from golden_shopping import SHOPPING_CASES
from scenario_settings import settings_of

from meal_planner.core.planner import next_shopping_trip, recompute
from meal_planner.core.recipes_data import ALL_RECIPES
from meal_planner.core.scheduling import build_schedule
from meal_planner.core.shopping import extract_shopping_list

BY_NAME = {s["name"]: settings_of(s) for s in SCENARIOS}


@pytest.mark.parametrize(
    "case", SHOPPING_CASES, ids=lambda c: f"{c['config']}-{c['today']}"
)
def test_next_shopping_trip_matches_golden(case):
    settings = settings_of(case)
    trip = next_shopping_trip(settings, date.fromisoformat(case["today"]))
    assert trip.cook_date.isoformat() == case["cook_date"]
    assert trip.window_start.isoformat() == case["window_start"]
    assert trip.window_end.isoformat() == case["window_end"] == case["next_cook_date"]
    assert trip.shopping_date.isoformat() == case["shopping_date"]
    assert [(i.lunch.id, i.dinner.id, i.portions) for i in trip.cook_items] == [
        (i["lunch"], i["dinner"], i["portions"]) for i in case["cook_items"]
    ]
    assert trip.shopping_groups == extract_shopping_list(
        build_schedule(settings, trip.window_start, trip.window_end)
    )


def test_trip_window_beyond_schedule():
    # Готовка по воскресеньям, today — понедельник сразу после неё:
    # окно поездки — следующая неделя, далеко за пределами 3-дневного расписания.
    settings = BY_NAME["single_1x_sunday"]
    today = date(2026, 11, 2)
    state = recompute(settings, today, date_to=today + timedelta(days=2))
    trip = state.next_shopping_trip
    assert trip.window_end == today + timedelta(days=13)
    assert state.schedule[-1].date < trip.window_start
    assert sum(i.portions for i in trip.cook_items) == 7
    assert trip.shopping_groups == extract_shopping_list(
        build_schedule(settings, trip.window_start, trip.window_end)
    )


def test_recompute():
    settings = BY_NAME["default_2x"]
    today = date(2026, 11, 9)
    state = recompute(settings, today, version=7)
    # по умолчанию — 14 дней с today, как PAGE_SIZE в JS
    assert [d.date for d in state.schedule] == [
        today + timedelta(days=i) for i in range(14)
    ]
    assert state.schedule == build_schedule(settings, today, today + timedelta(days=13))
    assert state.next_shopping_trip == next_shopping_trip(settings, today)
    assert state.recipes is ALL_RECIPES
    assert state.settings is settings
    assert state.today == today
    assert state.version == 7


def test_recompute_arbitrary_range_keeps_trip_from_today():
    settings = BY_NAME["default_2x"]
    today = date(2026, 11, 9)
    state = recompute(settings, today, date(2026, 12, 1), date(2026, 12, 3))
    assert [d.date for d in state.schedule] == [
        date(2026, 12, 1),
        date(2026, 12, 2),
        date(2026, 12, 3),
    ]
    assert state.next_shopping_trip == next_shopping_trip(settings, today)


def test_recompute_from_only_gives_14_days():
    state = recompute(BY_NAME["default_2x"], date(2026, 11, 9), date(2026, 12, 1))
    assert len(state.schedule) == 14
    assert state.schedule[0].date == date(2026, 12, 1)
