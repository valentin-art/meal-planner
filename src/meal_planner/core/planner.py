"""Оркестрация: Settings + сегодняшняя дата → полный AppState."""

from datetime import date, timedelta

from meal_planner.core.models import AppState, CookItem, Settings, ShoppingTrip
from meal_planner.core.recipes_data import ALL_RECIPES
from meal_planner.core.scheduling import (
    DINNER_IDS,
    LUNCH_IDS,
    build_schedule,
    cook_weekdays,
    nearest_cook_date,
)
from meal_planner.core.shopping import count_portions, extract_shopping_list


def next_shopping_trip(settings: Settings, today: date) -> ShoppingTrip:
    # JS ShoppingTab: едим со следующего дня после готовки по следующую готовку
    # включительно. Окно может закончиться через 13 дней после today, поэтому
    # дни строим здесь, а не берём из запрошенного расписания.
    weekdays = cook_weekdays(settings)
    cook_date = nearest_cook_date(weekdays, today)
    window_start = cook_date + timedelta(days=1)
    window_end = nearest_cook_date(weekdays, window_start)
    days = build_schedule(settings, window_start, window_end)
    cook_items = tuple(
        # Ужин — по позиции обеда в ротации, как в JS, а не из дня.
        CookItem(
            lunch=lunch,
            dinner=ALL_RECIPES[DINNER_IDS[LUNCH_IDS.index(lunch.id)]],
            portions=portions,
        )
        for lunch, portions in count_portions(d.lunch for d in days).items()
    )
    if settings.shopping_timing == "before":
        shopping_date = cook_date - timedelta(days=1)
    else:
        shopping_date = cook_date
    return ShoppingTrip(
        shopping_date=shopping_date,
        cook_date=cook_date,
        window_start=window_start,
        window_end=window_end,
        cook_items=cook_items,
        shopping_groups=extract_shopping_list(days),
    )


def recompute(
    settings: Settings, today: date, window_days: int = 14, version: int = 0
) -> AppState:
    """version приходит снаружи (из SQLite в API) — в core нет состояния."""
    return AppState(
        settings=settings,
        schedule=build_schedule(
            settings, today, today + timedelta(days=window_days - 1)
        ),
        next_shopping_trip=next_shopping_trip(settings, today),
        recipes=ALL_RECIPES,
        version=version,
    )
