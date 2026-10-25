from dataclasses import replace
from datetime import date

import pytest
from golden_scheduling import SCENARIOS
from scenario_settings import settings_of

from meal_planner.core.models import Settings, default_settings
from meal_planner.core.validation import (
    SettingsError,
    settings_errors,
    usable_settings,
)

VALID = Settings(date(2026, 11, 5), 2, 3, 6, "same")


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["name"])
def test_golden_settings_are_valid(scenario):
    errors = settings_errors(settings_of(scenario))
    if scenario["name"] == "same_weekday_twice":
        # Чт/Чт: в JS допустимо, с 6.8 — запрещено (расчёт в core для golden остаётся)
        assert errors == [
            SettingsError("cook_weekday2", "Два дня готовки должны различаться")
        ]
    else:
        assert errors == []


def test_same_second_day_is_fine_for_single_cook():
    assert settings_errors(replace(VALID, cook_count=1, cook_weekday2=3)) == []


def test_usable_settings_keeps_valid():
    assert usable_settings(VALID, date(2026, 11, 9)) == (VALID, [])


def test_usable_settings_falls_back_to_defaults_with_saved_anchor():
    broken = replace(VALID, cook_weekday2=3)  # Чт/Чт → Ср/Ср
    settings, errors = usable_settings(broken, date(2026, 11, 9))
    assert [e.field for e in errors] == ["cook_weekday2"]
    assert settings == replace(
        default_settings(date(2026, 11, 9)), meal_anchor_date=VALID.meal_anchor_date
    )
    assert settings_errors(settings) == []


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
