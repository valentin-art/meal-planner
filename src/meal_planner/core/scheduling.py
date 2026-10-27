"""Ротация блюд и даты готовки. Порт 1:1 функций из meal-plan.jsx.

Отличие с 6.5: до якоря блюд нет, первая готовка — день 0 (якорь − 1).
С 7.7: пропущенная готовка ставит цикл на паузу (skipped).
С 8.1: у дня с блюдами — отклонение от суточной цели.
"""

from collections.abc import Iterable
from dataclasses import replace
from datetime import date, timedelta

from meal_planner.core.models import (
    Catalog,
    CyclePosition,
    DayMeals,
    Recipe,
    Settings,
    TodayPosition,
)

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


def breakfast_for_date(d: date, anchor: date) -> str:
    return ROTATION_BREAKFASTS[(d - anchor).days % 2]


def fish_priority(catalog: Catalog, k: int) -> int:
    # 0, 1 или 2 — чем выше, тем раньше в партии блюдо съедается.
    slot = catalog.slot(k)
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
    natural_ks = [
        dish_index_for_date(window_start + timedelta(days=i), anchor)
        for i in range(win_len)
    ]
    # JS: sort((a, b) => fishPriority(b) - fishPriority(a) || a - b)
    order = sorted(
        range(len(natural_ks)),
        key=lambda i: (-fish_priority(catalog, natural_ks[i]), i),
    )
    sorted_ks = [natural_ks[i] for i in order]
    pos = (d - window_start).days
    k = sorted_ks[max(0, min(pos, len(sorted_ks) - 1))]

    slot = catalog.slot(k)
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
    days = (date_to - date_from).days + 1
    return tuple(
        with_target_deltas(
            meals_for_date(
                catalog, date_from + timedelta(days=i), anchor, weekdays, skipped
            ),
            settings,
        )
        for i in range(max(days, 0))
    )


def cook_window(settings: Settings, cook_date: date) -> tuple[date, date]:
    """Что ест партия готовки: со следующего дня по следующую готовку."""
    start = cook_date + timedelta(days=1)
    return start, nearest_cook_date(cook_weekdays(settings), start)


def slot_portions(days: Iterable[DayMeals]) -> dict[int, int]:
    """Сколько дней в окне у каждой позиции ротации, в порядке появления."""
    portions: dict[int, int] = {}
    for d in days:
        if d.cycle_index is not None:
            portions[d.cycle_index] = portions.get(d.cycle_index, 0) + 1
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
    for candidate in candidates:
        dish = meals_for_date(catalog, today, candidate, cook_weekdays).cycle_index
        if dish is not None:  # всегда: кандидаты не позже сегодня
            anchors.setdefault(dish, candidate)

    current = meals_for_date(catalog, today, anchor, cook_weekdays, skipped)
    return TodayPosition(
        current=0 if current.is_day_zero else current.cycle_index,
        options=tuple(
            CyclePosition(
                dish=dish,
                anchor=anchors[dish],
                lunch=catalog.recipes[catalog.slot(dish).lunch_id] if dish else None,
                dinner=catalog.recipes[catalog.slot(dish).dinner_id] if dish else None,
            )
            for dish in sorted(anchors)
        ),
    )
