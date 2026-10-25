"""Ротация блюд и даты готовки. Порт 1:1 функций из meal-plan.jsx.

Отличие с 6.5: до якоря блюд нет, первая готовка — день 0 (якорь − 1).
"""

from datetime import date, timedelta

from meal_planner.core.models import DayMeals, Recipe, Settings
from meal_planner.core.recipes_data import ALL_RECIPES

# Порядок 5-дневной ротации: позиция k (1..5) → обед/ужин LUNCH_IDS[k-1]/DINNER_IDS[k-1].
LUNCH_IDS = ("l1", "l2", "l3", "l4", "l5")
DINNER_IDS = ("d1", "d2", "d3", "d4", "d5")


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
    return "b1" if (d - anchor).days % 2 == 0 else "b2"


def fish_priority(k: int) -> int:
    # 0, 1 или 2 — чем выше, тем раньше в партии блюдо съедается.
    p = 0
    if ALL_RECIPES[LUNCH_IDS[k - 1]].is_fish:
        p += 1
    if ALL_RECIPES[DINNER_IDS[k - 1]].is_fish:
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


def day_zero(anchor: date) -> date:
    return anchor - timedelta(days=1)


def cook_date_for_trip(
    anchor: date, cook_weekdays: tuple[int, ...], today: date
) -> date:
    # До старта первая готовка — день 0, даже если это не день недели готовки (6.5).
    if today <= day_zero(anchor):
        return day_zero(anchor)
    return nearest_cook_date(cook_weekdays, today)


def meals_for_date(d: date, anchor: date, cook_weekdays: tuple[int, ...]) -> DayMeals:
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
        range(len(natural_ks)), key=lambda i: (-fish_priority(natural_ks[i]), i)
    )
    sorted_ks = [natural_ks[i] for i in order]
    pos = (d - window_start).days
    k = sorted_ks[max(0, min(pos, len(sorted_ks) - 1))]

    breakfast = ALL_RECIPES[breakfast_for_date(d, anchor)]
    lunch = ALL_RECIPES[LUNCH_IDS[k - 1]]
    dinner = ALL_RECIPES[DINNER_IDS[k - 1]]
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
        is_cook_day=d.isoweekday() in cook_weekdays,
        is_before_start=False,
    )


def build_schedule(
    settings: Settings, date_from: date, date_to: date
) -> tuple[DayMeals, ...]:
    """Расписание на date_from..date_to включительно."""
    anchor = settings.meal_anchor_date
    weekdays = cook_weekdays(settings)
    days = (date_to - date_from).days + 1
    return tuple(
        meals_for_date(date_from + timedelta(days=i), anchor, weekdays)
        for i in range(max(days, 0))
    )
