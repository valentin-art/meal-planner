"""8.1: отклонение итога дня от суточной цели по ккал и белку."""

from dataclasses import replace
from datetime import date

from meal_planner.core.actions import set_nutrition_target
from meal_planner.core.models import Settings
from meal_planner.core.planner import recompute
from meal_planner.core.scheduling import build_schedule
from meal_planner.core.seed import SEED_CATALOG
from meal_planner.core.validation import settings_errors

BASE = Settings(date(2026, 11, 5), 2, 3, 6, "same")
TARGET = replace(BASE, daily_kcal_target=2000, daily_protein_target=100)
WEEK = (date(2026, 11, 2), date(2026, 11, 15))


def schedule(settings):
    return build_schedule(SEED_CATALOG, settings, *WEEK)


def test_deviation_is_total_minus_target():
    days = [d for d in schedule(TARGET) if d.cycle_index is not None]
    assert days
    for d in days:
        assert d.kcal_delta == d.kcal_total - 2000
        assert d.protein_delta == d.protein_total - 100


def test_without_target_no_deviation_and_rest_unchanged():
    days = schedule(BASE)
    assert {(d.kcal_delta, d.protein_delta) for d in days} == {(None, None)}
    # С целью меняются только отклонения.
    assert [
        replace(d, kcal_delta=None, protein_delta=None) for d in schedule(TARGET)
    ] == list(days)


def test_targets_are_independent():
    kcal_only = replace(BASE, daily_kcal_target=2000)
    day = next(d for d in schedule(kcal_only) if d.cycle_index is not None)
    assert day.kcal_delta == day.kcal_total - 2000
    assert day.protein_delta is None


def test_no_deviation_on_days_without_meals():
    # До старта, день 0 (04.11) и пауза после пропущенной готовки 11.11.
    skipped = replace(TARGET, skipped_cook_date=date(2026, 11, 11))
    empty = [d for d in schedule(skipped) if d.cycle_index is None]
    assert {d.is_before_start for d in empty} == {True, False}
    assert any(d.is_day_zero for d in empty) and any(d.is_skipped for d in empty)
    assert {(d.kcal_delta, d.protein_delta) for d in empty} == {(None, None)}


def test_recompute_schedule_carries_deviation():
    state = recompute(SEED_CATALOG, TARGET, date(2026, 11, 9))
    assert all(d.kcal_delta == d.kcal_total - 2000 for d in state.schedule)


def test_set_nutrition_target_changes_only_targets():
    assert set_nutrition_target(BASE, 2000, 100) == TARGET
    assert set_nutrition_target(TARGET, None, None) == BASE
    assert set_nutrition_target(TARGET, 1800, None) == replace(
        BASE, daily_kcal_target=1800
    )


def test_target_limits():
    assert settings_errors(replace(BASE, daily_kcal_target=500)) == []
    assert settings_errors(replace(BASE, daily_protein_target=400)) == []
    errors = settings_errors(
        replace(BASE, daily_kcal_target=20000, daily_protein_target=5)
    )
    assert [(e.field, e.message) for e in errors] == [
        ("daily_kcal_target", "Цель по ккал — от 500 до 6000"),
        ("daily_protein_target", "Цель по белку — от 10 до 400 г"),
    ]
