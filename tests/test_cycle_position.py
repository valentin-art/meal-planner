"""6.6: «сегодня — блюдо №N» — сервер сам подбирает якорь."""

from datetime import date, timedelta
from itertools import combinations

import pytest

from meal_planner.core.actions import ActionError, set_cycle_position
from meal_planner.core.models import Settings
from meal_planner.core.scheduling import meals_for_date, today_position
from meal_planner.core.seed import SEED_CATALOG

MONDAY = date(2026, 11, 2)
WEEKDAY_SETS = [(w,) for w in range(1, 8)] + list(combinations(range(1, 8), 2))


@pytest.mark.parametrize("weekdays", WEEKDAY_SETS, ids=str)
def test_every_option_shows_its_dish_today(weekdays):
    for i in range(7):
        today = MONDAY + timedelta(days=i)
        options = today_position(
            SEED_CATALOG, today, date(2026, 1, 1), weekdays
        ).options
        assert options[0].dish == 0
        assert meals_for_date(
            SEED_CATALOG, today, options[0].anchor, weekdays
        ).is_day_zero
        for o in options[1:]:
            day = meals_for_date(SEED_CATALOG, today, o.anchor, weekdays)
            assert day.cycle_index == o.dish
            assert (o.lunch, o.dinner) == (day.lunch, day.dinner)
        assert [o.dish for o in options] == sorted({o.dish for o in options})


def test_fish_reorder_limits_options():
    # Готовка Ср/Сб, Чт — первый день окна Чт–Сб: первым съедается самое
    # «рыбное» блюдо партии, поэтому возможны только 3 и 5 (и день 0).
    options = today_position(
        SEED_CATALOG, date(2026, 11, 5), date(2026, 11, 5), (3, 6)
    ).options
    assert [o.dish for o in options] == [0, 3, 5]


def test_prefers_already_running_cycle():
    # Чт 5 — первый день окна; блюдо 3 даёт и якорь в начале окна (цикл идёт),
    # и якоря раньше — берётся ближайший к окну.
    options = today_position(
        SEED_CATALOG, date(2026, 11, 5), date(2026, 1, 1), (3, 6)
    ).options
    assert next(o.anchor for o in options if o.dish == 3) == date(2026, 11, 5)


@pytest.mark.parametrize(
    ("anchor", "current"),
    [
        (date(2026, 11, 20), None),  # цикл ещё не начался
        (date(2026, 11, 6), 0),  # сегодня день 0
        (date(2026, 11, 5), 3),  # сегодня — якорь, рыба вперёд
    ],
)
def test_current_position(anchor, current):
    assert (
        today_position(SEED_CATALOG, date(2026, 11, 5), anchor, (3, 6)).current
        == current
    )


SETTINGS = Settings(date(2026, 11, 20), 2, 3, 6, "same")


def test_set_cycle_position_sets_anchor():
    today = date(2026, 11, 5)
    moved = set_cycle_position(SEED_CATALOG, SETTINGS, today, 5)
    assert (
        meals_for_date(SEED_CATALOG, today, moved.meal_anchor_date, (3, 6)).cycle_index
        == 5
    )
    assert set_cycle_position(
        SEED_CATALOG, SETTINGS, today, 0
    ).meal_anchor_date == date(2026, 11, 6)
    assert (moved.cook_count, moved.cook_weekday1, moved.cook_weekday2) == (2, 3, 6)


def test_unreachable_dish_is_an_error():
    with pytest.raises(ActionError) as e:
        set_cycle_position(SEED_CATALOG, SETTINGS, date(2026, 11, 5), 1)
    assert e.value.field == "dish"
    assert e.value.message == "Сегодня может быть только: день 0, блюдо №3, блюдо №5"
