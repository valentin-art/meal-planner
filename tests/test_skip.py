"""Пропуск готовки (7.7): пауза цикла.

Новое правило, а не порт JSX: ожидания записаны как правило. Без пропуска
поведение прежнее — это держат golden-тесты.
"""

from dataclasses import replace
from datetime import date, timedelta

import pytest

from meal_planner.core.actions import (
    ActionError,
    set_anchor_date,
    set_cook_schedule,
    set_cycle_position,
    set_shopping_timing,
    skip_next_cook,
    unskip_cook,
)
from meal_planner.core.models import Settings
from meal_planner.core.planner import recompute
from meal_planner.core.scheduling import (
    cook_weekdays,
    dish_index_for_date,
    meals_for_date,
)
from meal_planner.core.validation import settings_errors

DAY = timedelta(days=1)

# (имя, настройки, сегодня, пропускаемая готовка C, конец паузы N)
LAYOUTS = [
    # Ср/Сб: окна 3 и 4 дня
    ("spread_2x", Settings(date(2026, 11, 5), 2, 3, 6, "same"), date(2026, 11, 9),
     date(2026, 11, 11), date(2026, 11, 14)),
    # Пн/Вт: окна 1 и 6 дней; сегодня Вт — пропускается сегодняшняя готовка
    ("adjacent_2x", Settings(date(2027, 3, 25), 2, 1, 2, "same"), date(2027, 3, 30),
     date(2027, 3, 30), date(2027, 4, 5)),
    # Вс раз в неделю: пауза 7 дней
    ("single_1x", Settings(date(2026, 11, 2), 1, 7, None, "same"), date(2026, 11, 4),
     date(2026, 11, 8), date(2026, 11, 15)),
]  # fmt: skip
IDS = [layout[0] for layout in LAYOUTS]
# Для одиночных тестов — раскладка Ср/Сб.
SETTINGS, TODAY, C, N = LAYOUTS[0][1:]


def dates(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += DAY


@pytest.mark.parametrize(("name", "settings", "today", "c", "n"), LAYOUTS, ids=IDS)
def test_skip_targets_next_trip_cook(name, settings, today, c, n):
    assert recompute(settings, today).next_shopping_trip.cook_date == c
    assert skip_next_cook(settings, today).skipped_cook_date == c


@pytest.mark.parametrize(("name", "settings", "today", "c", "n"), LAYOUTS, ids=IDS)
def test_pause_keeps_cooked_days_and_resumes_with_same_dish(
    name, settings, today, c, n
):
    anchor, wd = settings.meal_anchor_date, cook_weekdays(settings)
    # До пропущенной готовки — всё как было, сварено уже по этому плану.
    for d in dates(anchor, c - DAY):
        assert meals_for_date(d, anchor, wd, c) == meals_for_date(d, anchor, wd)
    # В день C едим старую партию, но не готовим.
    on_c = meals_for_date(c, anchor, wd, c)
    assert on_c == replace(meals_for_date(c, anchor, wd), is_cook_day=False)
    # Пауза: блюд нет; в её последний день — снова готовка.
    for d in dates(c + DAY, n):
        day = meals_for_date(d, anchor, wd, c)
        assert (day.lunch, day.dinner, day.breakfast) == (None, None, None)
        assert day.is_skipped and day.is_cook_day == (d == n)
    # После паузы — тот же цикл, сдвинутый на её длину: с блюда, на котором встал.
    shifted = anchor + (n - c)
    assert dish_index_for_date(n + DAY, shifted) == dish_index_for_date(c + DAY, anchor)
    for d in dates(n + DAY, n + 21 * DAY):
        assert meals_for_date(d, anchor, wd, c) == meals_for_date(d, shifted, wd)


@pytest.mark.parametrize(("name", "settings", "today", "c", "n"), LAYOUTS, ids=IDS)
def test_trip_goes_to_cook_after_pause(name, settings, today, c, n):
    skipped = skip_next_cook(settings, today)
    trip = recompute(skipped, today).next_shopping_trip
    assert (trip.shopping_date, trip.cook_date) == (n, n)
    assert trip.window_start == n + DAY
    assert trip.cook_items  # в окне после паузы снова есть блюда

    before = recompute(set_shopping_timing(skipped, "before"), today)
    assert before.next_shopping_trip.cook_date == n
    assert before.next_shopping_trip.shopping_date == n - DAY


@pytest.mark.parametrize(("name", "settings", "today", "c", "n"), LAYOUTS, ids=IDS)
def test_past_pause_settles_into_anchor(name, settings, today, c, n):
    skipped = skip_next_cook(settings, today)
    later = n + DAY
    state = recompute(skipped, later, later, later + 13 * DAY)
    assert state.settings.skipped_cook_date is None
    assert state.settings.meal_anchor_date == settings.meal_anchor_date + (n - c)
    assert state.skipped_cook is None
    # Блюда после паузы не поменялись от того, что пропуск ушёл в якорь.
    with_skip = recompute(skipped, n, later, later + 13 * DAY).schedule
    assert state.schedule == with_skip


def test_state_shows_skip_and_pause():
    state = recompute(skip_next_cook(SETTINGS, TODAY), TODAY)
    assert state.skipped_cook is not None
    assert (state.skipped_cook.cook_date, state.skipped_cook.pause_end) == (C, N)
    assert state.skipped_cook.pause_start == C + DAY
    assert state.skipped_cook.can_undo
    assert not state.can_skip_next_cook
    assert recompute(SETTINGS, TODAY).can_skip_next_cook

    in_pause = recompute(skip_next_cook(SETTINGS, TODAY), C + DAY)
    assert not in_pause.skipped_cook.can_undo
    assert in_pause.today_position.current is None  # сегодня блюд нет


def test_only_one_skip_and_not_before_start():
    skipped = skip_next_cook(SETTINGS, TODAY)
    with pytest.raises(ActionError, match="Уже пропущена готовка ср 11.11"):
        skip_next_cook(skipped, TODAY)
    # Цикл ещё не начался: ближайшая готовка — день 0.
    not_started = replace(SETTINGS, meal_anchor_date=TODAY + 3 * DAY)
    with pytest.raises(ActionError, match="Цикл ещё не начался"):
        skip_next_cook(not_started, TODAY)
    assert not recompute(not_started, TODAY).can_skip_next_cook


def test_unskip_until_the_cook_day():
    skipped = skip_next_cook(SETTINGS, TODAY)
    assert unskip_cook(skipped, TODAY) == SETTINGS
    assert unskip_cook(skipped, C) == SETTINGS  # в сам день готовки ещё можно
    with pytest.raises(ActionError, match="Пауза уже идёт"):
        unskip_cook(skipped, C + DAY)
    with pytest.raises(ActionError, match="Пропущенной готовки нет"):
        unskip_cook(SETTINGS, TODAY)


def test_position_and_anchor_changes_drop_the_skip():
    skipped = skip_next_cook(SETTINGS, TODAY)
    assert set_anchor_date(skipped, TODAY).skipped_cook_date is None
    # Из паузы возвращаются раньше, выбрав блюдо на сегодня.
    back = set_cycle_position(skipped, C + DAY, 0)
    assert back.skipped_cook_date is None
    assert recompute(back, C + DAY).today_position.current == 0


def test_cook_schedule_change_drops_the_skip():
    skipped = skip_next_cook(SETTINGS, TODAY)
    # Пауза ещё не началась — просто снимается, якорь прежний.
    before = set_cook_schedule(skipped, TODAY, 2, 2, 5)
    assert before.skipped_cook_date is None
    assert before.meal_anchor_date == SETTINGS.meal_anchor_date
    # Пауза идёт — засчитывается: цикл после неё тот же.
    during = set_cook_schedule(skipped, C + DAY, 2, 2, 5)
    assert during.skipped_cook_date is None
    assert during.meal_anchor_date == SETTINGS.meal_anchor_date + (N - C)


@pytest.mark.parametrize(
    "skipped",
    [date(2026, 11, 10), date(2026, 11, 4)],  # не день готовки; до якоря
    ids=["not_cook_day", "before_anchor"],
)
def test_validation_rejects_bad_skip(skipped):
    settings = Settings(date(2026, 11, 5), 2, 3, 6, "same", skipped)
    assert [e.field for e in settings_errors(settings)] == ["skipped_cook_date"]
