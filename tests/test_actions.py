from datetime import date

from meal_planner.core.actions import set_cook_schedule, set_shopping_timing
from meal_planner.core.models import Settings

BASE = Settings(date(2026, 11, 5), 2, 3, 6, "same")


def test_set_cook_schedule_changes_only_schedule():
    assert set_cook_schedule(BASE, 2, 1, 4) == Settings(
        date(2026, 11, 5), 2, 1, 4, "same"
    )


def test_second_day_survives_switching_to_one_cook_and_back():
    # Как в JS: при 1 готовке второй день остаётся сохранённым.
    single = set_cook_schedule(BASE, 1, 7)
    assert (single.cook_count, single.cook_weekday1, single.cook_weekday2) == (1, 7, 6)
    double = set_cook_schedule(single, 2, 3)
    assert (double.cook_count, double.cook_weekday1, double.cook_weekday2) == (2, 3, 6)


def test_set_shopping_timing_changes_only_timing():
    assert set_shopping_timing(BASE, "before") == Settings(
        date(2026, 11, 5), 2, 3, 6, "before"
    )
