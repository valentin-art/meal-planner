"""Перемешивание цикла (8.4a).

Новое правило, а не порт JSX: ожидания записаны как правило. Без строк
перемешивания порядок 1→5 — это держат golden-тесты.
"""

from dataclasses import replace
from datetime import date, timedelta
from itertools import combinations

import pytest

from meal_planner.core.actions import (
    ActionError,
    reset_cycle_order,
    reshuffle_cycle,
    set_cook_schedule,
    skip_next_cook,
)
from meal_planner.core.cooking import batch_dishes
from meal_planner.core.models import CycleOrder, CycleShuffle, Settings
from meal_planner.core.planner import next_shopping_trip, recompute
from meal_planner.core.scheduling import (
    build_schedule,
    cook_weekdays,
    cook_window,
    first_free_cycle,
    first_free_day,
    meals_for_date,
    slot_for_index,
    tape_date,
    tape_index,
    today_position,
    without_skip,
)
from meal_planner.core.seed import SEED_CATALOG
from meal_planner.core.shuffle import (
    ALL_ORDERS,
    NATURAL,
    ROTATIONS,
    cycle_order,
    fits_after,
    shuffled_after,
)
from meal_planner.core.validation import settings_errors

DAY = timedelta(days=1)
# Ср/Сб, якорь — чт 05.11: окна чт–сб (3 дня) и вс–ср (4 дня).
BASE = Settings(date(2026, 11, 5), 2, 3, 6, "same")
WEEKDAY_PAIRS = list(combinations(range(1, 8), 2))


def shuffled(settings: Settings, *rows: tuple[int, int | None]) -> Settings:
    return replace(settings, cycle_shuffles=tuple(CycleShuffle(c, s) for c, s in rows))


def tape(shuffles, length: int) -> list[int]:
    return [slot_for_index(shuffles, i) for i in range(length)]


def batches(settings: Settings, date_from: date, date_to: date):
    """Дни каждой партии с готовкой в date_from..date_to."""
    weekdays = cook_weekdays(settings)
    d = date_from
    while d <= date_to:
        if d.isoweekday() in weekdays and d >= settings.meal_anchor_date:
            yield build_schedule(SEED_CATALOG, settings, *cook_window(settings, d))
        d += DAY


# --- правило стыка ---


def test_every_order_has_16_fitting_successors():
    for prev in ALL_ORDERS:
        assert sum(fits_after(prev, o) for o in ALL_ORDERS) == 16


def test_fits_after_is_four_distinct_days():
    for prev in ALL_ORDERS:
        for order in ALL_ORDERS:
            joined = prev + order
            distinct = all(len(set(joined[i : i + 4])) == 4 for i in range(7))
            assert fits_after(prev, order) == distinct


def test_shuffled_never_repeats_previous_order():
    for prev in ALL_ORDERS:
        for n in range(3):
            order = shuffled_after(prev, 42, n)
            assert order != prev
            assert fits_after(prev, order)


def test_no_shuffles_is_natural_rotation():
    assert tape((), 15) == [1, 2, 3, 4, 5] * 3


def test_pinned_orders_for_seed():
    # Закреплено: смена алгоритма (хэш, порядок кандидатов) поменяла бы план
    # у пользователя после обновления образа.
    rows = (CycleShuffle(2, 42),)
    assert [cycle_order(rows, n) for n in range(5)] == [
        (1, 2, 3, 4, 5),
        (1, 2, 3, 4, 5),
        (2, 3, 4, 1, 5),
        (3, 4, 2, 5, 1),
        (4, 2, 5, 1, 3),
    ]


@pytest.mark.parametrize("seed", range(20))
def test_tape_rules(seed):
    rows = (CycleShuffle(1, seed), CycleShuffle(9, seed + 1000))
    t = tape(rows, 5 * 30)
    for n in range(30):
        assert sorted(t[5 * n : 5 * n + 5]) == list(NATURAL)  # цикл — перестановка
    for i in range(len(t) - 3):
        assert len(set(t[i : i + 4])) == 4  # любые 4 дня подряд — разные


# --- партии ---


@pytest.mark.parametrize("weekdays", WEEKDAY_PAIRS, ids=str)
@pytest.mark.parametrize("seed", [1, 7, 42])
def test_batches_have_no_repeats_and_fish_first(weekdays, seed):
    settings = shuffled(
        replace(BASE, cook_weekday1=weekdays[0], cook_weekday2=weekdays[1]), (0, seed)
    )
    for days in batches(settings, date(2026, 11, 5), date(2027, 1, 31)):
        ks = [d.cycle_index for d in days]
        if len(ks) <= 4:
            assert len(set(ks)) == len(ks), (weekdays, days[0].date)
        fish = [d.lunch.is_fish + d.dinner.is_fish for d in days]
        assert fish == sorted(fish, reverse=True)


# --- что заморожено ---


@pytest.mark.parametrize(
    ("today", "first_free"),
    [
        # пн 09.11: ближайшая закупка — ср 11.11, окно с чт 12.11 (лента 7) —
        # не куплена; цикл, начинающийся не раньше, — 2 (с 15.11).
        (date(2026, 11, 9), 2),
        # ср 11.11 — день закупки: считается купленной, свободно с вс 15.11
        # (лента 10) — цикл 2.
        (date(2026, 11, 11), 2),
        # чт 12.11: закупка сб 14.11, окно с вс 15.11 — цикл 2.
        (date(2026, 11, 12), 2),
        # сб 14.11 — день закупки: свободно с чт 19.11 (лента 14) — цикл 3.
        (date(2026, 11, 14), 3),
    ],
)
def test_first_free_cycle(today, first_free):
    assert first_free_cycle(BASE, today) == first_free


def test_first_free_cycle_before_start_is_zero():
    assert first_free_cycle(BASE, date(2026, 10, 1)) == 0


@pytest.mark.parametrize("timing", ["same", "before"])
@pytest.mark.parametrize("today_offset", range(14))
def test_reshuffle_keeps_bought_days_and_trip(timing, today_offset):
    today = date(2026, 11, 3) + timedelta(days=today_offset)
    before = replace(BASE, shopping_timing=timing)
    for current in (before, reshuffle_cycle(before, today, 1)):
        after = reshuffle_cycle(current, today, 99)
        changes_from = recompute(SEED_CATALOG, after, today).cycle_order.changes_from
        # Меняется с начала партии нового цикла — не раньше первого
        # некупленного дня, а он не раньше окна ближайшей закупки.
        assert changes_from >= first_free_day(current, today) > today
        assert build_schedule(SEED_CATALOG, after, today, changes_from - DAY) == (
            build_schedule(SEED_CATALOG, current, today, changes_from - DAY)
        )
        trip = next_shopping_trip(SEED_CATALOG, current, today)
        if today >= trip.shopping_date:
            # В день закупки она куплена — поездка не меняется вся.
            assert next_shopping_trip(SEED_CATALOG, after, today) == trip


def test_reshuffle_changes_order_from_first_free_cycle():
    today = date(2026, 11, 9)
    after = reshuffle_cycle(BASE, today, 42)
    assert after.cycle_shuffles == (CycleShuffle(2, 42),)
    start = tape_date(after, 10)  # вс 15.11
    assert start == date(2026, 11, 15)
    old = build_schedule(SEED_CATALOG, BASE, start, start + 4 * DAY)
    new = build_schedule(SEED_CATALOG, after, start, start + 4 * DAY)
    assert old != new


def test_reshuffle_twice_replaces_same_cycle():
    today = date(2026, 11, 9)
    twice = reshuffle_cycle(reshuffle_cycle(BASE, today, 1), today, 2)
    assert twice.cycle_shuffles == (CycleShuffle(2, 2),)


def test_reshuffle_later_keeps_earlier_rows():
    first = reshuffle_cycle(BASE, date(2026, 11, 9), 1)
    later = reshuffle_cycle(first, date(2026, 11, 20), 2)
    assert later.cycle_shuffles == (CycleShuffle(2, 1), CycleShuffle(4, 2))


# --- обратно к 1→5 ---


def test_reset_returns_to_rotation_within_one_cycle():
    for seed in range(30):
        rows = (CycleShuffle(0, seed), CycleShuffle(4, None))
        assert cycle_order(rows, 5) in ROTATIONS
        assert cycle_order(rows, 9) == cycle_order(rows, 5)
        t = tape(rows, 50)
        for i in range(len(t) - 3):
            assert len(set(t[i : i + 4])) == 4


def test_reset_without_shuffle_is_422():
    with pytest.raises(ActionError, match="1→5"):
        reset_cycle_order(BASE, date(2026, 11, 9))
    off = reset_cycle_order(
        reshuffle_cycle(BASE, date(2026, 11, 9), 1), date(2026, 11, 20)
    )
    with pytest.raises(ActionError, match="1→5"):
        reset_cycle_order(off, date(2026, 11, 20))


# --- устойчивость ---


def test_settled_skip_keeps_future_days():
    today = date(2026, 11, 9)
    settings = shuffled(BASE, (1, 7))
    skipping = skip_next_cook(settings, today)  # ср 11.11, пауза по сб 14.11
    assert skipping.skipped_cook_date == date(2026, 11, 11)
    settled = without_skip(skipping)
    window = (date(2026, 11, 15), date(2026, 12, 31))
    assert build_schedule(SEED_CATALOG, settled, *window) == build_schedule(
        SEED_CATALOG, skipping, *window
    )


def test_tape_index_skips_pause():
    skipping = replace(BASE, skipped_cook_date=date(2026, 11, 11))
    # Пауза чт 12.11–сб 14.11: вс 15.11 — тот же день ленты, что был чт 12.11.
    assert tape_index(skipping, date(2026, 11, 15)) == 7
    assert tape_index(skipping, date(2026, 11, 13)) == 7
    assert tape_date(skipping, 7) == date(2026, 11, 15)
    assert tape_date(skipping, 6) == date(2026, 11, 11)


def test_cook_days_change_keeps_tape():
    settings = shuffled(BASE, (1, 7))
    moved = set_cook_schedule(settings, date(2026, 11, 9), 2, 1, 4)
    assert moved.cycle_shuffles == settings.cycle_shuffles
    # Партии другие, но каждая ест свои дни той же ленты.
    for days in batches(moved, date(2026, 11, 9), date(2026, 12, 31)):
        slots = [
            slot_for_index(moved.cycle_shuffles, tape_index(moved, d.date))
            for d in days
        ]
        assert sorted(d.cycle_index for d in days) == sorted(slots)


def test_today_position_options_hold_under_shuffle():
    shuffles = (CycleShuffle(0, 5),)
    for weekdays in [(3, 6), (1, 4), (2,)]:
        for i in range(14):
            today = date(2026, 11, 2) + timedelta(days=i)
            options = today_position(
                SEED_CATALOG, today, date(2026, 10, 1), weekdays, shuffles=shuffles
            ).options
            for o in options[1:]:
                day = meals_for_date(
                    SEED_CATALOG, today, o.anchor, weekdays, shuffles=shuffles
                )
                assert day.cycle_index == o.dish


# --- состояние и валидация ---


def test_cycle_order_state():
    today = date(2026, 11, 9)
    assert recompute(SEED_CATALOG, BASE, today).cycle_order == CycleOrder(False, None)
    after = reshuffle_cycle(BASE, today, 42)
    assert recompute(SEED_CATALOG, after, today).cycle_order == CycleOrder(
        True, date(2026, 11, 15)
    )
    started = recompute(SEED_CATALOG, after, date(2026, 11, 15)).cycle_order
    assert started == CycleOrder(True, None)
    # Пн 16.11: свободно с чт 19.11 (лента 14), цикл 3 — с пт 20.11; его
    # партия (чт–сб) меняется с чт 19.11.
    off = reset_cycle_order(after, date(2026, 11, 16))
    assert off.cycle_shuffles[-1] == CycleShuffle(3, None)
    assert recompute(SEED_CATALOG, off, date(2026, 11, 16)).cycle_order == (
        CycleOrder(False, date(2026, 11, 19))
    )


@pytest.mark.parametrize(
    "rows",
    [
        ((-1, 1),),  # цикл до якоря
        ((3, 1), (2, 1)),  # не по возрастанию
        ((2, 1), (2, 5)),  # повтор цикла
        ((2, 2**31),),  # зерно вне пределов
    ],
)
def test_broken_shuffles_are_settings_errors(rows):
    errors = settings_errors(shuffled(BASE, *rows))
    assert [e.field for e in errors] == ["cycle_shuffles"]


def test_valid_shuffles_pass():
    assert settings_errors(shuffled(BASE, (0, 0), (3, None), (5, 2**31 - 1))) == []


def test_cooking_batch_follows_shuffle():
    # Партия для списания (8.3b) — те же блюда и порции, что «Что готовить».
    settings = shuffled(BASE, (0, 3))
    for cook_date in (date(2026, 11, 11), date(2026, 11, 14)):
        trip = next_shopping_trip(SEED_CATALOG, settings, cook_date)
        planned: dict[str, int] = {}
        for item in trip.cook_items:
            for recipe in (item.lunch, item.dinner):
                planned[recipe.id] = planned.get(recipe.id, 0) + item.portions
        dishes = batch_dishes(SEED_CATALOG, settings, cook_date)
        assert {r.id: n for r, n in dishes} == planned
