"""Ротация блюд и даты готовки. Порт 1:1 функций из meal-plan.jsx.

Отличие с 6.5: до якоря блюд нет, первая готовка — день 0 (якорь − 1).
С 7.7: пропущенная готовка ставит цикл на паузу (skipped).
С 8.1: у дня с блюдами — отклонение от суточной цели.
С 8.4a: порядок слотов в цикле может быть перемешан (core/shuffle.py).
С 8.4b: у позиции может быть несколько вариантов — Catalog.slot_at(k, n).
С 8.5: порции обедов и ужинов — под цель по ккал (core/portions.py).
"""

from collections.abc import Iterable
from dataclasses import replace
from datetime import date, timedelta

from meal_planner.core.models import (
    Catalog,
    CyclePosition,
    CycleShuffle,
    CycleSlot,
    DayMeals,
    Recipe,
    Settings,
    TodayPosition,
)
from meal_planner.core.portions import scaled_meal, target_for
from meal_planner.core.shuffle import CYCLE_LEN, cycle_order

# Позиция ротации k (1..5) → обед и ужин слота k каталога (5.3); завтраки
# чередуются b1/b2 и в слоты не входят (5.1).
ROTATION_BREAKFASTS = ("b1", "b2")


def cook_weekdays(settings: Settings) -> tuple[int, ...]:
    # JS: cookWeekdays в MealPlanApp — при cook_count=1 второй день игнорируется.
    if settings.cook_count == 1:
        return (settings.cook_weekday1,)
    if settings.cook_weekday2 is None:
        # В JS дало бы [wd1, undefined] и «готовку» каждый день; из UI недостижимо.
        raise ValueError("cook_weekday2 обязателен при cook_count=2")
    return (settings.cook_weekday1, settings.cook_weekday2)


def dish_index_for_date(d: date, anchor: date) -> int:
    # JS: (((diff % 5) + 5) % 5) + 1. В Python % с положительным делителем
    # не бывает отрицательным, поэтому diff % 5 + 1 — то же самое.
    return (d - anchor).days % 5 + 1


def slot_for_index(shuffles: tuple[CycleShuffle, ...], i: int) -> int:
    """Слот дня ленты i (дней от якоря): без перемешивания — i % 5 + 1, как
    dish_index_for_date."""
    return cycle_order(shuffles, i // CYCLE_LEN)[i % CYCLE_LEN]


def breakfast_for_date(d: date, anchor: date) -> str:
    return ROTATION_BREAKFASTS[(d - anchor).days % 2]


def fish_priority(catalog: Catalog, slot: CycleSlot) -> int:
    # 0, 1 или 2 — чем выше, тем раньше в партии блюдо съедается.
    p = 0
    if catalog.recipes[slot.lunch_id].is_fish:
        p += 1
    if catalog.recipes[slot.dinner_id].is_fish:
        p += 1
    return p


def previous_cook_date(cook_weekdays: tuple[int, ...], from_date: date) -> date:
    # Строго раньше from_date. Запасной ответ недостижим при валидных днях недели.
    for i in range(1, 8):
        d = from_date - timedelta(days=i)
        if d.isoweekday() in cook_weekdays:
            return d
    return from_date - timedelta(days=7)


def next_date_for_weekday(weekday: int, from_date: date) -> date:
    # Включая from_date. Запасной ответ недостижим при weekday в 1..7.
    for i in range(7):
        d = from_date + timedelta(days=i)
        if d.isoweekday() == weekday:
            return d
    return from_date


def nearest_cook_date(cook_weekdays: tuple[int, ...], from_date: date) -> date:
    # JS: цикл с `d < best` — при равенстве остаётся первый, как и у min().
    return min(next_date_for_weekday(wd, from_date) for wd in cook_weekdays)


def day_totals(breakfast: Recipe, lunch: Recipe, dinner: Recipe) -> tuple[int, int]:
    parts = (breakfast, lunch, dinner)
    return sum(r.kcal for r in parts), sum(r.protein for r in parts)


def with_target_deltas(day: DayMeals, settings: Settings) -> DayMeals:
    """Отклонение итога дня от цели (8.1): плюс — больше цели.

    Только у дней с блюдами: день 0, дни до старта и пауза — не «−2000».
    """
    if day.cycle_index is None:
        return day
    kcal, protein = settings.daily_kcal_target, settings.daily_protein_target
    return replace(
        day,
        kcal_delta=None if kcal is None else day.kcal_total - kcal,
        protein_delta=None if protein is None else day.protein_total - protein,
    )


def day_zero(anchor: date) -> date:
    return anchor - timedelta(days=1)


def cook_date_for_trip(
    anchor: date, cook_weekdays: tuple[int, ...], today: date
) -> date:
    # До старта первая готовка — день 0, даже если это не день недели готовки (6.5).
    if today <= day_zero(anchor):
        return day_zero(anchor)
    return nearest_cook_date(cook_weekdays, today)


def pause_end(skipped: date, cook_weekdays: tuple[int, ...]) -> date:
    """Пауза после пропущенной готовки — по следующую готовку включительно."""
    return nearest_cook_date(cook_weekdays, skipped + timedelta(days=1))


def meals_for_date(
    catalog: Catalog,
    d: date,
    anchor: date,
    cook_weekdays: tuple[int, ...],
    skipped: date | None = None,
    shuffles: tuple[CycleShuffle, ...] = (),
) -> DayMeals:
    if d < anchor:
        # Цикл ещё не начался (6.5): до дня 0 ничего не готовим и не едим,
        # в день 0 — первая готовка.
        is_day_zero = d == day_zero(anchor)
        return DayMeals(
            date=d,
            cycle_index=None,
            breakfast=None,
            lunch=None,
            dinner=None,
            kcal_total=0,
            protein_total=0,
            is_day_zero=is_day_zero,
            is_cook_day=is_day_zero,
            is_before_start=not is_day_zero,
        )
    if skipped is not None and d > skipped:
        end = pause_end(skipped, cook_weekdays)
        if d <= end:
            # Пауза (7.7): готовки skipped не было — до следующей не едим дома.
            return DayMeals(
                date=d,
                cycle_index=None,
                breakfast=None,
                lunch=None,
                dinner=None,
                kcal_total=0,
                protein_total=0,
                is_day_zero=False,
                is_cook_day=d == end,
                is_before_start=False,
                is_skipped=True,
            )
        # После паузы цикл продолжается с того же блюда, на котором встал.
        anchor += end - skipped
    # Какие блюда попадают в партию, решает обычная 5-дневная ротация, поэтому
    # состав закупки не меняется. Меняется только порядок внутри партии: рыбные
    # блюда — на самые свежие дни, мясные — в конец (дольше хранятся).
    window_end = nearest_cook_date(cook_weekdays, d)
    # Первое окно — партия дня 0: начинается с якоря, а не с дней до старта.
    window_start = max(
        previous_cook_date(cook_weekdays, window_end) + timedelta(days=1), anchor
    )
    win_len = (window_end - window_start).days + 1
    start_index = (window_start - anchor).days
    # Позиция дня ленты i и её обед с ужином в цикле i // 5 (8.4b).
    natural = [
        catalog.slot_at(slot_for_index(shuffles, i), i // CYCLE_LEN)
        for i in range(start_index, start_index + win_len)
    ]
    # JS: sort((a, b) => fishPriority(b) - fishPriority(a) || a - b)
    order = sorted(
        range(len(natural)),
        key=lambda i: (-fish_priority(catalog, natural[i]), i),
    )
    pos = (d - window_start).days
    slot = natural[order[max(0, min(pos, len(order) - 1))]]
    k = slot.slot

    breakfast = catalog.recipes[breakfast_for_date(d, anchor)]
    lunch = catalog.recipes[slot.lunch_id]
    dinner = catalog.recipes[slot.dinner_id]
    kcal, protein = day_totals(breakfast, lunch, dinner)
    return DayMeals(
        date=d,
        cycle_index=k,
        breakfast=breakfast,
        lunch=lunch,
        dinner=dinner,
        kcal_total=kcal,
        protein_total=protein,
        is_day_zero=False,
        is_cook_day=d.isoweekday() in cook_weekdays and d != skipped,
        is_before_start=False,
    )


def build_schedule(
    catalog: Catalog, settings: Settings, date_from: date, date_to: date
) -> tuple[DayMeals, ...]:
    """Расписание на date_from..date_to включительно."""
    anchor = settings.meal_anchor_date
    weekdays = cook_weekdays(settings)
    skipped = settings.skipped_cook_date
    shuffles = settings.cycle_shuffles
    days = (date_to - date_from).days + 1
    return tuple(
        with_target_deltas(
            with_portions(
                catalog,
                settings,
                meals_for_date(
                    catalog,
                    date_from + timedelta(days=i),
                    anchor,
                    weekdays,
                    skipped,
                    shuffles,
                ),
            ),
            settings,
        )
        for i in range(max(days, 0))
    )


def with_portions(catalog: Catalog, settings: Settings, day: DayMeals) -> DayMeals:
    """Обед и ужин в порциях под цель партии этого дня (8.5)."""
    if day.lunch is None or day.dinner is None or not settings.portion_scales:
        return day
    target = target_for(settings.portion_scales, batch_start(settings, day.date))
    if target is None:
        return day
    lunch = scaled_meal(catalog, day.lunch, target, ROTATION_BREAKFASTS)
    dinner = scaled_meal(catalog, day.dinner, target, ROTATION_BREAKFASTS)
    assert day.breakfast is not None  # у дня с обедом завтрак есть
    kcal, protein = day_totals(day.breakfast, lunch, dinner)
    return replace(
        day, lunch=lunch, dinner=dinner, kcal_total=kcal, protein_total=protein
    )


def cook_window(settings: Settings, cook_date: date) -> tuple[date, date]:
    """Что ест партия готовки: со следующего дня по следующую готовку."""
    start = cook_date + timedelta(days=1)
    return start, nearest_cook_date(cook_weekdays(settings), start)


def slot_portions(days: Iterable[DayMeals]) -> dict[tuple[str, str], int]:
    """Сколько дней в окне у каждой пары «обед + ужин», в порядке появления.

    По паре, а не по позиции (8.4b): одна позиция из двух циклов с разными
    вариантами — два разных блюда.
    """
    portions: dict[tuple[str, str], int] = {}
    for d in days:
        if d.lunch is not None and d.dinner is not None:
            pair = (d.lunch.id, d.dinner.id)
            portions[pair] = portions.get(pair, 0) + 1
    return portions


def trip_cook_date(settings: Settings, today: date) -> date:
    """Готовка, к которой ближайшая закупка."""
    weekdays = cook_weekdays(settings)
    cook_date = cook_date_for_trip(settings.meal_anchor_date, weekdays, today)
    if settings.shopping_timing == "before" and cook_date == today:
        # Закупка для сегодняшней готовки была вчера (в JS показывалась
        # вчерашняя дата) — ближайшая поездка уже к следующей готовке (6.8).
        cook_date = nearest_cook_date(weekdays, today + timedelta(days=1))
    if cook_date == settings.skipped_cook_date:
        # Пропущенную готовку не закупаем: следующая — в конце паузы (7.7).
        cook_date = pause_end(cook_date, weekdays)
    return cook_date


def shopping_date_for(settings: Settings, cook_date: date) -> date:
    if settings.shopping_timing == "before":
        return cook_date - timedelta(days=1)
    return cook_date


def settle_skip(settings: Settings, today: date) -> Settings:
    """Прошедшая пауза уходит в якорь: блюда после неё те же, пропуск свободен.

    Дни до пропуска при этом показываются по новому якорю — это прошлое.
    """
    skipped = settings.skipped_cook_date
    if skipped is None:
        return settings
    end = pause_end(skipped, cook_weekdays(settings))
    if end >= today:
        return settings
    return without_skip(settings)


def without_skip(settings: Settings) -> Settings:
    """Снять пропуск, засчитав паузу: цикл продолжается с того же блюда."""
    skipped = settings.skipped_cook_date
    if skipped is None:
        return settings
    end = pause_end(skipped, cook_weekdays(settings))
    return replace(
        settings,
        meal_anchor_date=settings.meal_anchor_date + (end - skipped),
        skipped_cook_date=None,
    )


def today_position(
    catalog: Catalog,
    today: date,
    anchor: date,
    cook_weekdays: tuple[int, ...],
    skipped: date | None = None,
    shuffles: tuple[CycleShuffle, ...] = (),
) -> TodayPosition:
    """Позиция «сегодня — блюдо N» (6.6): текущая и все достижимые.

    Из-за перестановки рыбы в партии сегодня может стоять не любое из 5 блюд,
    поэтому варианты перебираются: каждому достижимому блюду — один якорь.
    Порядок кандидатов: сначала «цикл уже идёт» (якорь не позже начала
    текущего окна, от ближайшего), потом «начался внутри окна».
    Варианты — без пропуска: выбор позиции его снимает (7.7); в паузе
    current — None.
    """
    window_end = nearest_cook_date(cook_weekdays, today)
    window_start = previous_cook_date(cook_weekdays, window_end) + timedelta(days=1)
    candidates = [window_start - timedelta(days=r) for r in range(5)] + [
        window_start + timedelta(days=i)
        for i in range(1, (today - window_start).days + 1)
    ]
    anchors = {0: today + timedelta(days=1)}  # сегодня день 0: готовка, цикл с завтра
    days: dict[int, DayMeals] = {}
    for candidate in candidates:
        day = meals_for_date(
            catalog, today, candidate, cook_weekdays, shuffles=shuffles
        )
        if day.cycle_index is not None:  # всегда: кандидаты не позже сегодня
            anchors.setdefault(day.cycle_index, candidate)
            days.setdefault(day.cycle_index, day)

    current = meals_for_date(catalog, today, anchor, cook_weekdays, skipped, shuffles)
    return TodayPosition(
        current=0 if current.is_day_zero else current.cycle_index,
        options=tuple(
            CyclePosition(
                dish=dish,
                anchor=anchors[dish],
                # Обед и ужин — варианта цикла при этом якоре (8.4b).
                lunch=days[dish].lunch if dish else None,
                dinner=days[dish].dinner if dish else None,
            )
            for dish in sorted(anchors)
        ),
    )


def tape_index(settings: Settings, d: date) -> int:
    """День ленты: сколько дней от якоря, без паузы пропущенной готовки (7.7).

    Для дня в паузе — как у первого дня после неё.
    """
    skipped = settings.skipped_cook_date
    anchor = settings.meal_anchor_date
    if skipped is not None and d > skipped:
        end = pause_end(skipped, cook_weekdays(settings))
        d = max(d, end + timedelta(days=1))
        anchor += end - skipped
    return (d - anchor).days


def tape_date(settings: Settings, i: int) -> date:
    """Обратное к tape_index: дата дня ленты i."""
    d = settings.meal_anchor_date + timedelta(days=i)
    skipped = settings.skipped_cook_date
    if skipped is not None and d > skipped:
        d += pause_end(skipped, cook_weekdays(settings)) - skipped
    return d


def first_free_day(settings: Settings, today: date) -> date:
    """Первый день, под который ещё не было закупки (8.4a); settings — после
    settle_skip.

    Ближайшая закупка не куплена, пока не настал её день; в день закупки она
    считается сделанной.
    """
    cook_date = trip_cook_date(settings, today)
    window_start, window_end = cook_window(settings, cook_date)
    if today >= shopping_date_for(settings, cook_date):
        return window_end + timedelta(days=1)
    return window_start


def first_free_cycle(settings: Settings, today: date) -> int:
    """Первый цикл, начинающийся не раньше first_free_day.

    Его партия может начаться раньше цикла (рыба переставляет дни внутри
    партии), но не раньше first_free_day: тот — начало партии.
    """
    i = tape_index(settings, first_free_day(settings, today))
    return max(0, -(-i // CYCLE_LEN))


def batch_start(settings: Settings, d: date) -> date:
    """Первый день партии, которая ест d (d — день с блюдами)."""
    weekdays = cook_weekdays(settings)
    window_end = nearest_cook_date(weekdays, d)
    start = previous_cook_date(weekdays, window_end) + timedelta(days=1)
    return max(start, settings.meal_anchor_date)
