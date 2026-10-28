"""Порции обедов и ужинов под цель по ккал (8.5).

Каждый обед и ужин доводится до доли (цель − средний завтрак) / 2:
множитель рецепта = доля / ккал рецепта, до 0.05, в пределах 0.5–2.0.
Завтраки не масштабируются. Множитель — свойство рецепта при данной цели,
поэтому в партии все порции блюда одинаковые.

Какая цель действует — строки PortionScale по началу партии: правка
приходит с первой некупленной партии и не делит партию надвое.
"""

from collections.abc import Iterable
from dataclasses import replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from meal_planner.core.models import Catalog, PortionScale, Recipe
from meal_planner.core.nutrition import nutrition

SCALE_STEP = Decimal("0.05")
SCALE_MIN, SCALE_MAX = Decimal("0.5"), Decimal(2)


def target_for(rows: Iterable[PortionScale], batch_start: date) -> int | None:
    """Цель масштаба партии, начинающейся batch_start; None — без масштаба."""
    target = None
    for row in rows:  # по from_date
        if row.from_date <= batch_start:
            target = row.kcal_target
    return target


def meal_share(catalog: Catalog, target: int, breakfast_ids: Iterable[str]) -> Decimal:
    """Сколько ккал на обед и на ужин: (цель − средний завтрак ротации) / 2."""
    kcal = [Decimal(catalog.recipes[rid].kcal) for rid in breakfast_ids]
    return (Decimal(target) - sum(kcal, Decimal(0)) / len(kcal)) / 2


def scale_for(recipe: Recipe, share: Decimal) -> Decimal:
    """Множитель: доля / ккал, до 0.05 (половина вверх), 0.5–2.0."""
    if recipe.kcal <= 0:
        return Decimal(1)  # без ккал масштабировать не от чего
    steps = (share / recipe.kcal / SCALE_STEP).quantize(Decimal(1), ROUND_HALF_UP)
    return min(max(steps * SCALE_STEP, SCALE_MIN), SCALE_MAX)


def scaled(catalog: Catalog, recipe: Recipe, factor: Decimal) -> Recipe:
    """Рецепт с количествами × factor; ккал и белок — заново по справочнику."""
    if factor == 1:
        return recipe
    ingredients = tuple(
        replace(i, qty=float(Decimal(str(i.qty)) * factor)) for i in recipe.ingredients
    )
    kcal, protein = nutrition(catalog.ingredients, ingredients)
    return replace(
        recipe,
        ingredients=ingredients,
        kcal=kcal,
        protein=protein,
        scale=float(factor),
    )


def scaled_meal(
    catalog: Catalog, recipe: Recipe, target: int | None, breakfast_ids: Iterable[str]
) -> Recipe:
    if target is None:
        return recipe
    share = meal_share(catalog, target, breakfast_ids)
    return scaled(catalog, recipe, scale_for(recipe, share))
