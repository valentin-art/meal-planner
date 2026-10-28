"""Оркестрация: Settings + сегодняшняя дата → полный AppState."""

from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from decimal import Decimal
from types import MappingProxyType

from meal_planner.core.actions import skip_next_cook_error
from meal_planner.core.models import (
    AppState,
    Catalog,
    CookItem,
    CycleOrder,
    PlanUse,
    Settings,
    ShoppingTrip,
    SkippedCook,
    SlotState,
    SlotVersion,
)
from meal_planner.core.scheduling import (
    batch_start,
    build_schedule,
    cook_weekdays,
    cook_window,
    pause_end,
    settle_skip,
    shopping_date_for,
    slot_portions,
    tape_date,
    today_position,
    trip_cook_date,
)
from meal_planner.core.shopping import extract_shopping_list
from meal_planner.core.shuffle import CYCLE_LEN
from meal_planner.core.stock import StockedTrip, StockLine, stock_in_catalog_units

SCHEDULE_DAYS = 14  # JS PAGE_SIZE


def next_shopping_trip(
    catalog: Catalog,
    settings: Settings,
    today: date,
    stock: Mapping[str, Decimal] | None = None,
) -> ShoppingTrip:
    # JS ShoppingTab: едим со следующего дня после готовки по следующую готовку
    # включительно. Окно может закончиться через 13 дней после today, поэтому
    # дни строим здесь, а не берём из запрошенного расписания.
    cook_date = trip_cook_date(settings, today)
    window_start, window_end = cook_window(settings, cook_date)
    days = build_schedule(catalog, settings, window_start, window_end)
    # Готовится по парам «обед + ужин» в порядке первого появления (JS брал
    # ужин по позиции обеда; с 5.3 рецепт может стоять в нескольких слотах,
    # с 8.4b у позиции бывает несколько вариантов).
    cook_items = tuple(
        CookItem(
            lunch=catalog.recipes[lunch_id],
            dinner=catalog.recipes[dinner_id],
            portions=n,
        )
        for (lunch_id, dinner_id), n in slot_portions(days).items()
    )
    return ShoppingTrip(
        shopping_date=shopping_date_for(settings, cook_date),
        cook_date=cook_date,
        window_start=window_start,
        window_end=window_end,
        cook_items=cook_items,
        shopping_groups=extract_shopping_list(catalog, days, stock),
    )


def shopping_with_stock(
    catalog: Catalog, settings: Settings, today: date, lines: Iterable[StockLine]
) -> StockedTrip:
    """Ближайшая закупка за вычетом остатков клиента (8.2).

    Та же поездка, что в recompute; остатки не сохраняются — они вход.
    """
    stock, ignored = stock_in_catalog_units(catalog, lines)
    trip = next_shopping_trip(catalog, settle_skip(settings, today), today, stock)
    return StockedTrip(trip=trip, ignored=tuple(ignored))


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


def changes_from(settings: Settings, today: date, from_cycle: int) -> date | None:
    """С какого дня действует правка с цикла from_cycle; None — уже действует.

    С начала партии первого дня цикла: рыба переставляет дни внутри партии.
    """
    start = batch_start(settings, tape_date(settings, from_cycle * CYCLE_LEN))
    return start if start > today else None


def slot_states(
    catalog: Catalog, settings: Settings, today: date
) -> tuple[SlotState, ...]:
    """Последняя версия каждой позиции (8.4b) — то, что правят в UI."""
    latest: dict[int, SlotVersion] = {}
    for v in catalog.slot_versions:  # по позиции и from_cycle
        latest[v.slot] = v
    return tuple(
        SlotState(
            slot=k,
            lunch_ids=v.lunch_ids,
            dinner_ids=v.dinner_ids,
            # С цикла 0 — состав с начала: купленного до него нет, дата не нужна.
            changes_from=changes_from(settings, today, v.from_cycle)
            if v.from_cycle > 0
            else None,
        )
        for k, v in sorted(latest.items())
    )


def cycle_order_state(settings: Settings, today: date) -> CycleOrder:
    shuffles = settings.cycle_shuffles
    if not shuffles:
        return CycleOrder(shuffled=False, changes_from=None)
    last = shuffles[-1]
    return CycleOrder(
        shuffled=last.seed is not None,
        changes_from=changes_from(settings, today, last.from_cycle),
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
            settings.cycle_shuffles,
        ),
        skipped_cook=skipped_cook(settings, today),
        can_skip_next_cook=skip_next_cook_error(settings, today) is None,
        plan_use=MappingProxyType(plan_use(catalog, settings, today, trip)),
        slots=slot_states(catalog, settings, today),
        cycle_order=cycle_order_state(settings, today),
    )
