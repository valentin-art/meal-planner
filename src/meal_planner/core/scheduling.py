"""Ротация блюд и даты готовки. Порт 1:1 функций из meal-plan.jsx."""

from datetime import date, timedelta

from meal_planner.core.models import Settings
from meal_planner.core.recipes_data import ALL_RECIPES

# Порядок 5-дневной ротации: позиция k (1..5) → обед/ужин LUNCH_IDS[k-1]/DINNER_IDS[k-1].
LUNCH_IDS = ("l1", "l2", "l3", "l4", "l5")
DINNER_IDS = ("d1", "d2", "d3", "d4", "d5")


def cook_weekdays(settings: Settings) -> tuple[int, ...]:
    # JS: cookWeekdays в MealPlanApp — при cook_count=1 второй день игнорируется.
    if settings.cook_count == 1:
        return (settings.cook_weekday1,)
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
    best = None
    for wd in cook_weekdays:
        d = next_date_for_weekday(wd, from_date)
        if best is None or d < best:
            best = d
    return best
