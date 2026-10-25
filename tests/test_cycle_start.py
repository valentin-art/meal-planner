"""Правило 6.5: до якоря блюд нет, первая готовка — день 0 (якорь − 1)."""

from datetime import date, timedelta

import pytest
from golden_scheduling import SCENARIOS
from scenario_settings import settings_of

from meal_planner.core.models import Settings
from meal_planner.core.planner import next_shopping_trip
from meal_planner.core.scheduling import build_schedule, cook_weekdays, meals_for_date
from meal_planner.core.shopping import count_portions, extract_shopping_list

# Якорь Пн 9 нояб., готовка Ср/Сб: день 0 — Вс 8, не день готовки.
ANCHOR = date(2026, 11, 9)
DAY0 = date(2026, 11, 8)
MID = Settings(ANCHOR, 2, 3, 6, "same")


def day(d: date, settings: Settings = MID):
    return meals_for_date(d, settings.meal_anchor_date, cook_weekdays(settings))


def test_before_start_has_no_meals_even_on_cook_weekday():
    saturday = day(date(2026, 11, 7))  # Сб — день недели готовки
    assert saturday.is_before_start
    assert not saturday.is_cook_day
    assert not saturday.is_day_zero
    assert (saturday.cycle_index, saturday.lunch, saturday.dinner) == (None, None, None)
    assert (saturday.kcal_total, saturday.protein_total) == (0, 0)


def test_day_zero_is_first_cook_without_meals():
    zero = day(DAY0)
    assert zero.is_day_zero and zero.is_cook_day
    assert not zero.is_before_start
    assert zero.breakfast is None and zero.cycle_index is None


def test_first_window_starts_at_anchor():
    # Окно партии дня 0 — Пн 9 … Ср 11, позиции 1..3; рыбные (3, 2) — первыми.
    assert [day(ANCHOR + timedelta(i)).cycle_index for i in range(3)] == [3, 2, 1]


@pytest.mark.parametrize("today", [date(2026, 11, 1), date(2026, 11, 6), DAY0], ids=str)
def test_trip_before_start_cooks_on_day_zero(today):
    trip = next_shopping_trip(MID, today)
    assert (trip.cook_date, trip.window_start, trip.window_end) == (
        DAY0,
        ANCHOR,
        date(2026, 11, 11),
    )
    assert trip.shopping_date == DAY0


def test_trip_after_start_is_regular():
    trip = next_shopping_trip(MID, ANCHOR)
    assert trip.cook_date == date(2026, 11, 11)  # Ср — день недели готовки


def test_shopping_before_is_day_before_day_zero():
    trip = next_shopping_trip(Settings(ANCHOR, 2, 3, 6, "before"), date(2026, 11, 6))
    assert trip.shopping_date == date(2026, 11, 7)


def test_anchor_on_cook_weekday_gives_one_day_first_window():
    # Якорь Ср 11 — день готовки: партии дня 0 хватает на один день,
    # в день якоря — снова готовка (решение 6.5: «как в тикете»).
    settings = Settings(date(2026, 11, 11), 2, 3, 6, "same")
    trip = next_shopping_trip(settings, date(2026, 11, 9))
    assert (trip.cook_date, trip.window_start, trip.window_end) == (
        date(2026, 11, 10),
        date(2026, 11, 11),
        date(2026, 11, 11),
    )
    assert day(date(2026, 11, 11), settings).is_cook_day


def test_shopping_list_skips_days_without_meals():
    days = build_schedule(MID, date(2026, 11, 1), date(2026, 11, 11))
    from_anchor = build_schedule(MID, ANCHOR, date(2026, 11, 11))
    assert extract_shopping_list(days) == extract_shopping_list(from_anchor)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["name"])
def test_day_zero_batch_is_what_is_eaten(scenario):
    # Закупка до старта и расписание первого окна согласованы: что готовим
    # в день 0, то и едим до следующей готовки.
    settings = settings_of(scenario)
    trip = next_shopping_trip(settings, settings.meal_anchor_date - timedelta(days=3))
    eaten = build_schedule(settings, trip.window_start, trip.window_end)
    assert trip.window_start == settings.meal_anchor_date
    assert [(i.lunch, i.portions) for i in trip.cook_items] == list(
        count_portions(d.lunch for d in eaten).items()
    )
