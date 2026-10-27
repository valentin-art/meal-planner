"""Оркестрация: Settings + сегодняшняя дата → полный AppState."""

from datetime import date, timedelta
from types import MappingProxyType

from meal_planner.core.actions import skip_next_cook_error
from meal_planner.core.models import (
    AppState,
    Catalog,
    CookItem,
    PlanUse,
    Settings,
    ShoppingTrip,
    SkippedCook,
)
from meal_planner.core.scheduling import (
    build_schedule,
    cook_weekdays,
    nearest_cook_date,
    pause_end,
    settle_skip,
    today_position,
    trip_cook_date,
)
from meal_planner.core.shopping import extract_shopping_list

SCHEDULE_DAYS = 14  # JS PAGE_SIZE


def next_shopping_trip(
    catalog: Catalog, settings: Settings, today: date
) -> ShoppingTrip:
    # JS ShoppingTab: едим со следующего дня после готовки по следующую готовку
    # включительно. Окно может закончиться через 13 дней после today, поэтому
    # дни строим здесь, а не берём из запрошенного расписания.
    weekdays = cook_weekdays(settings)
    cook_date = trip_cook_date(settings, today)
    window_start = cook_date + timedelta(days=1)
    window_end = nearest_cook_date(weekdays, window_start)
    days = build_schedule(catalog, settings, window_start, window_end)
    # Готовится по позициям ротации в порядке первого появления; обед и ужин —
    # из слота (JS брал ужин по позиции обеда — то же, пока обеды в слотах
    # разные; с 5.3 один рецепт может стоять в нескольких слотах).
    portions: dict[int, int] = {}
    for d in days:
        if d.cycle_index is not None:
            portions[d.cycle_index] = portions.get(d.cycle_index, 0) + 1
    cook_items = tuple(
        CookItem(
            lunch=catalog.recipes[catalog.slot(k).lunch_id],
            dinner=catalog.recipes[catalog.slot(k).dinner_id],
            portions=n,
        )
        for k, n in portions.items()
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
        shopping_groups=extract_shopping_list(catalog, days),
    )


def plan_use(
    catalog: Catalog, settings: Settings, today: date, trip: ShoppingTrip
) -> dict[str, PlanUse]:
    """Какие рецепты уже в плане (5.1): правка их меняет купленное или список.

    "bought" — съедаются с сегодня до окна ближайшей закупки: продукты уже
    куплены; "next_trip" — в окне ближайшей закупки. "bought" важнее.
    """
    result: dict[str, PlanUse] = {}
    eaten = build_schedule(
        catalog, settings, today, trip.window_start - timedelta(days=1)
    )
    window = build_schedule(catalog, settings, trip.window_start, trip.window_end)
    for use, days in (("bought", eaten), ("next_trip", window)):
        for day in days:
            for recipe in (day.breakfast, day.lunch, day.dinner):
                if recipe is not None:
                    result.setdefault(recipe.id, use)
    return result


def skipped_cook(settings: Settings, today: date) -> SkippedCook | None:
    skipped = settings.skipped_cook_date
    if skipped is None:
        return None
    return SkippedCook(
        cook_date=skipped,
        pause_start=skipped + timedelta(days=1),
        pause_end=pause_end(skipped, cook_weekdays(settings)),
        can_undo=skipped >= today,
    )


def recompute(
    catalog: Catalog,
    settings: Settings,
    today: date,
    date_from: date | None = None,
    date_to: date | None = None,
    version: int = 0,
) -> AppState:
    """Расписание на date_from..date_to (по умолчанию 14 дней с today, как
    PAGE_SIZE в JS); поездка в магазин — всегда от today. version и каталог
    приходят снаружи (из SQLite в API) — в core нет состояния."""
    date_from = date_from or today
    date_to = date_to or date_from + timedelta(days=SCHEDULE_DAYS - 1)
    settings = settle_skip(settings, today)
    trip = next_shopping_trip(catalog, settings, today)
    return AppState(
        today=today,
        settings=settings,
        schedule=build_schedule(catalog, settings, date_from, date_to),
        next_shopping_trip=trip,
        version=version,
        today_position=today_position(
            catalog,
            today,
            settings.meal_anchor_date,
            cook_weekdays(settings),
            settings.skipped_cook_date,
        ),
        skipped_cook=skipped_cook(settings, today),
        can_skip_next_cook=skip_next_cook_error(settings, today) is None,
        plan_use=MappingProxyType(plan_use(catalog, settings, today, trip)),
        slots=catalog.slots,
    )
