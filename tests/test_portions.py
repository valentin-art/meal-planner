"""Порции обедов и ужинов под цель по ккал (8.5).

Новое правило, а не порт JSX: ожидания записаны как правило. Без строк
масштаба порции как в рецептах — это держат golden-тесты.
"""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from itertools import combinations

import pytest

from meal_planner.core.actions import (
    ActionError,
    set_nutrition_target,
    set_portion_scaling,
    skip_next_cook,
)
from meal_planner.core.cooking import (
    batch_dishes,
    check_enough,
    cooked_change,
    counter_update,
)
from meal_planner.core.models import (
    CycleShuffle,
    PortionScale,
    PortionScaling,
    Settings,
)
from meal_planner.core.nutrition import nutrition
from meal_planner.core.planner import next_shopping_trip, recompute
from meal_planner.core.portions import meal_share, scale_for, scaled, target_for
from meal_planner.core.scheduling import (
    batch_start,
    build_schedule,
    cook_weekdays,
    cook_window,
    first_free_day,
    without_skip,
)
from meal_planner.core.seed import SEED_CATALOG
from meal_planner.core.stock import StockLine

DAY = timedelta(days=1)
# Ср/Сб, якорь — чт 05.11: окна чт–сб (3 дня) и вс–ср (4 дня). Пн 09.11:
# закупка ср 11.11 не куплена — первая некупленная партия с чт 12.11.
BASE = Settings(date(2026, 11, 5), 2, 3, 6, "same", daily_kcal_target=2700)
TODAY = date(2026, 11, 9)
SCALED = replace(BASE, portion_scales=(PortionScale(date(2026, 11, 5), 2700),))


# --- множитель ---


def test_share_and_scales_at_2700():
    # (2700 − (540 + 735) / 2) / 2 = 1031.25 на обед и на ужин.
    share = meal_share(SEED_CATALOG, 2700, ("b1", "b2"))
    assert share == Decimal("1031.25")
    scales = {
        rid: scale_for(SEED_CATALOG.recipes[rid], share)
        for rid in ("l1", "l2", "l3", "d1", "d3", "d5")
    }
    assert scales == {
        "l1": Decimal("1.45"),  # 1031.25 / 700 = 1.473
        "l2": Decimal("1.40"),  # / 740 = 1.394
        "l3": Decimal("1.50"),  # / 685 = 1.505
        "d1": Decimal("1.65"),  # / 620 = 1.663
        "d3": Decimal("1.85"),  # / 565 = 1.825 — половина вверх
        "d5": Decimal("1.40"),  # / 750 = 1.375 — половина вверх
    }


@pytest.mark.parametrize(
    ("share", "expected"),
    [("100", "0.5"), ("5000", "2"), ("700", "1"), ("717.5", "1.05")],
)
def test_scale_limits_and_rounding(share, expected):
    l1 = SEED_CATALOG.recipes["l1"]  # 700 ккал
    assert scale_for(l1, Decimal(share)) == Decimal(expected)


def test_scaled_recipe_multiplies_quantities_and_recounts_kcal():
    l1 = SEED_CATALOG.recipes["l1"]
    big = scaled(SEED_CATALOG, l1, Decimal("1.45"))
    assert (big.id, big.scale) == ("l1", 1.45)
    assert [i.qty for i in big.ingredients] == [
        float(Decimal(str(i.qty)) * Decimal("1.45")) for i in l1.ingredients
    ]
    assert (big.kcal, big.protein) == nutrition(
        SEED_CATALOG.ingredients, big.ingredients
    )
    assert scaled(SEED_CATALOG, l1, Decimal(1)) is l1


def test_target_for_takes_row_by_batch_start():
    rows = (
        PortionScale(date(2026, 11, 12), 2700),
        PortionScale(date(2026, 11, 19), None),
    )
    assert target_for(rows, date(2026, 11, 8)) is None
    assert target_for(rows, date(2026, 11, 12)) == 2700
    assert target_for(rows, date(2026, 11, 15)) == 2700
    assert target_for(rows, date(2026, 11, 19)) is None


# --- расписание ---


def test_no_rows_keeps_recipes():
    window = date(2026, 11, 1), date(2027, 1, 31)
    off = replace(BASE, portion_scales=(PortionScale(date(2026, 11, 5), None),))
    assert build_schedule(SEED_CATALOG, off, *window) == build_schedule(
        SEED_CATALOG, BASE, *window
    )
    for d in build_schedule(SEED_CATALOG, BASE, *window):
        if d.breakfast and d.lunch and d.dinner:
            assert (d.lunch.scale, d.dinner.scale, d.breakfast.scale) == (1, 1, 1)


def test_days_land_near_target():
    days = build_schedule(SEED_CATALOG, SCALED, date(2026, 11, 5), date(2026, 12, 31))
    for d in days:
        assert d.breakfast is not None
        assert d.breakfast.scale == 1  # завтраки не масштабируются
        # Разброс — завтраки b1/b2 (±97.5) и округление множителя (±0.025 × ккал).
        assert abs(d.kcal_total - 2700) <= 160, d.date
        assert d.kcal_delta == d.kcal_total - 2700


def test_shopping_follows_scaled_portions():
    trip = next_shopping_trip(SEED_CATALOG, SCALED, TODAY)
    plain = next_shopping_trip(SEED_CATALOG, BASE, TODAY)
    total = {i.name: i.qty for g in trip.shopping_groups for i in g.items}
    base = {i.name: i.qty for g in plain.shopping_groups for i in g.items}
    assert total["куриное филе"] > base["куриное филе"] * 1.3
    assert all(c.lunch.scale != 1 and c.dinner.scale != 1 for c in trip.cook_items)


# --- с первой некупленной партии ---


@pytest.mark.parametrize("timing", ["same", "before"])
@pytest.mark.parametrize("today_offset", range(14))
def test_scaling_keeps_bought_days_and_trip(timing, today_offset):
    today = date(2026, 11, 3) + timedelta(days=today_offset)
    before = replace(BASE, shopping_timing=timing)
    after = set_portion_scaling(before, today, True)
    first = first_free_day(before, today)
    assert after.portion_scales == (PortionScale(first, 2700),)
    assert build_schedule(SEED_CATALOG, after, today, first - DAY) == (
        build_schedule(SEED_CATALOG, before, today, first - DAY)
    )
    changed = build_schedule(SEED_CATALOG, after, first, first + 6 * DAY)
    assert any(d.lunch and d.lunch.scale != 1 for d in changed)
    trip = next_shopping_trip(SEED_CATALOG, before, today)
    if today >= trip.shopping_date:
        assert next_shopping_trip(SEED_CATALOG, after, today) == trip


@pytest.mark.parametrize("weekdays", list(combinations(range(1, 8), 2)), ids=str)
def test_batch_never_mixes_two_scales(weekdays):
    # Масштаб меняется по началу партии — и при перемешивании 8.4a.
    settings = replace(
        BASE,
        cook_weekday1=weekdays[0],
        cook_weekday2=weekdays[1],
        cycle_shuffles=(CycleShuffle(0, 3),),
        portion_scales=(
            PortionScale(date(2026, 11, 13), 2700),
            PortionScale(date(2026, 11, 24), 2200),
            PortionScale(date(2026, 12, 3), None),
        ),
    )
    d = date(2026, 11, 5)
    while d < date(2026, 12, 20):
        if d.isoweekday() in cook_weekdays(settings):
            days = build_schedule(SEED_CATALOG, settings, *cook_window(settings, d))
            scales: dict[str, set[float]] = {}
            recipes = [r for day in days for r in (day.lunch, day.dinner) if r]
            assert len(recipes) == 2 * len(days)
            for r in recipes:
                scales.setdefault(r.id, set()).add(r.scale)
            assert all(len(s) == 1 for s in scales.values()), (weekdays, d)
            target = target_for(
                settings.portion_scales, batch_start(settings, days[0].date)
            )
            for r in recipes:
                plain = SEED_CATALOG.recipes[r.id]
                expected = (
                    Decimal(1)
                    if target is None
                    else scale_for(
                        plain, meal_share(SEED_CATALOG, target, ("b1", "b2"))
                    )
                )
                assert Decimal(str(r.scale)) == expected
        d += DAY


def test_target_change_follows_when_scaling_is_on():
    on = set_portion_scaling(BASE, TODAY, True)
    assert on.portion_scales == (PortionScale(date(2026, 11, 12), 2700),)
    # Через неделю — новая цель: новая строка с первой некупленной партии.
    later = set_nutrition_target(on, date(2026, 11, 16), 2400, None)
    assert later.portion_scales == (
        PortionScale(date(2026, 11, 12), 2700),
        PortionScale(date(2026, 11, 19), 2400),
    )
    # Цель снята — масштаб выключается с той же партии.
    off = set_nutrition_target(on, date(2026, 11, 16), None, None)
    assert off.portion_scales[-1] == PortionScale(date(2026, 11, 19), None)
    # Масштаб выключен — цель строк не добавляет.
    assert set_nutrition_target(BASE, TODAY, 2400, None).portion_scales == ()


def test_pending_change_is_cancelled_not_stacked():
    on = set_portion_scaling(BASE, TODAY, True)
    assert set_portion_scaling(on, TODAY, False).portion_scales == ()
    assert set_portion_scaling(on, TODAY, True) == on  # то же состояние


def test_scaling_needs_kcal_target():
    with pytest.raises(ActionError, match="Сначала задайте цель по ккал"):
        set_portion_scaling(replace(BASE, daily_kcal_target=None), TODAY, True)


def test_settled_skip_keeps_future_portions():
    on = set_portion_scaling(BASE, TODAY, True)
    skipping = skip_next_cook(on, TODAY)  # ср 11.11, пауза по сб 14.11
    window = date(2026, 11, 15), date(2026, 12, 31)
    assert build_schedule(SEED_CATALOG, without_skip(skipping), *window) == (
        build_schedule(SEED_CATALOG, skipping, *window)
    )


def test_state_shows_scaling_and_scaled_recipes():
    on = set_portion_scaling(BASE, TODAY, True)
    state = recompute(SEED_CATALOG, on, TODAY)
    assert state.portion_scaling == PortionScaling(True, 2700, date(2026, 11, 12))
    keys = [(r.id, r.scale) for r in state.scaled_recipes]
    assert len(keys) == len(set(keys))
    assert all(scale != 1 for _, scale in keys)
    shown = {
        (r.id, r.scale)
        for d in state.schedule
        for r in (d.lunch, d.dinner)
        if r is not None and r.scale != 1
    }
    assert shown <= set(keys)
    assert recompute(SEED_CATALOG, BASE, TODAY).portion_scaling == PortionScaling(
        False, None, None
    )


# --- списание (8.3b) ---


def test_write_off_uses_scaled_portion():
    cook_date = date(2026, 11, 14)
    dishes = {r.id: r for r, _ in batch_dishes(SEED_CATALOG, SCALED, cook_date)}
    assert all(r.scale != 1 for r in dishes.values())
    change = cooked_change(SEED_CATALOG, SCALED, cook_date, set(), "l1", True)
    assert change.recipe == dishes["l1"]
    chicken = next(i for i in change.recipe.ingredients if i.name == "куриное филе")
    plain = next(
        i for i in SEED_CATALOG.recipes["l1"].ingredients if i.name == "куриное филе"
    )
    assert chicken.qty == plain.qty * 1.45
    eid = SEED_CATALOG.ingredients["куриное филе"].id
    line = StockLine(eid, 5000, "г", "input_number.chicken")
    update = counter_update(SEED_CATALOG, change.recipe, 2, [line], -1)
    assert dict(update.values)["input_number.chicken"] == pytest.approx(
        5000 - chicken.qty * 2
    )
    short = StockLine(eid, plain.qty * 2, "г", "input_number.chicken")
    with pytest.raises(ActionError, match="куриное филе"):
        check_enough(SEED_CATALOG, change.recipe, 2, [short])
