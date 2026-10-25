from dataclasses import replace
from datetime import date

import pytest
from golden_scheduling import SCENARIOS
from scenario_settings import settings_of

from meal_planner.core.models import Settings
from meal_planner.core.validation import settings_errors

VALID = Settings(date(2026, 11, 5), 2, 3, 6, "same")


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["name"])
def test_golden_settings_are_valid(scenario):
    # включая same_weekday_twice (Чт/Чт) — в JS это допустимо
    assert settings_errors(settings_of(scenario)) == []


def test_single_cook_without_second_day_is_valid():
    assert settings_errors(replace(VALID, cook_count=1, cook_weekday2=None)) == []


@pytest.mark.parametrize(
    ("change", "field", "message"),
    [
        ({"cook_count": 3}, "cook_count", "Готовок в неделю может быть 1 или 2"),
        (
            {"cook_weekday1": 0},
            "cook_weekday1",
            "День готовки — число от 1 (пн) до 7 (вс)",
        ),
        (
            {"cook_weekday1": 8},
            "cook_weekday1",
            "День готовки — число от 1 (пн) до 7 (вс)",
        ),
        (
            {"cook_weekday2": None},
            "cook_weekday2",
            "При двух готовках в неделю нужен второй день готовки",
        ),
        (
            {"cook_weekday2": 9},
            "cook_weekday2",
            "День готовки — число от 1 (пн) до 7 (вс)",
        ),
        (
            {"shopping_timing": "later"},
            "shopping_timing",
            "Закупка — в день готовки или за день до",
        ),
    ],
)
def test_each_rule(change, field, message):
    errors = settings_errors(replace(VALID, **change))
    assert [(e.field, e.message) for e in errors] == [(field, message)]
