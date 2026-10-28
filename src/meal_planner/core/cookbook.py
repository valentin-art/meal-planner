"""Книга рецептов для дашборда «Кухня» (9.1): ближайшая готовка и активные
рецепты по видам.

Дашборд только раскладывает: какие блюда готовить, с какой порцией и что
показывать в группах — решает core.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType

from meal_planner.core.cooking import batch_dishes
from meal_planner.core.models import Catalog, MealKind, Recipe, Settings
from meal_planner.core.scheduling import (
    cook_date_for_trip,
    cook_weekdays,
    pause_end,
    settle_skip,
)


@dataclass(frozen=True, slots=True)
class UpcomingDish:
    recipe: Recipe  # с порцией под цель (8.5): количества и ккал уже умножены
    planned_portions: int  # сколько дней блюдо едят по плану


@dataclass(frozen=True, slots=True)
class UpcomingCook:
    cook_date: date
    dishes: tuple[UpcomingDish, ...]  # обеды и ужины, по рецепту в порядке появления


def upcoming_cook_date(settings: Settings, today: date) -> date:
    """Ближайшая готовка не раньше today; settings — после settle_skip.

    До старта — день 0; пропущенная (7.7) — конец паузы. В отличие от
    trip_cook_date, без поправки shopping_timing="before": нужна готовка, а не
    закупка. В день готовки — сегодня, даже если всё уже отмечено.
    """
    weekdays = cook_weekdays(settings)
    cook_date = cook_date_for_trip(settings.meal_anchor_date, weekdays, today)
    if cook_date == settings.skipped_cook_date:
        cook_date = pause_end(cook_date, weekdays)
    return cook_date


def upcoming_cook(catalog: Catalog, settings: Settings, today: date) -> UpcomingCook:
    settings = settle_skip(settings, today)
    cook_date = upcoming_cook_date(settings, today)
    return UpcomingCook(
        cook_date,
        tuple(
            UpcomingDish(r, n) for r, n in batch_dishes(catalog, settings, cook_date)
        ),
    )


def cookbook_recipes(catalog: Catalog) -> Mapping[MealKind, tuple[Recipe, ...]]:
    """Активные рецепты по видам — без черновиков (8.6a) и архива (5.1).

    Порядок — каталога: по номеру id (catalog_order), l2 раньше l10.
    """
    active = [r for r in catalog.recipes.values() if r.status == "active"]
    return MappingProxyType(
        {kind: tuple(r for r in active if r.kind == kind) for kind in MealKind}
    )
