"""Список покупок: подсчёт порций, агрегация ингредиентов, группировка.

Порт aggregate/groupByCategory/fmtQty и подсчёта порций из ShoppingTab
(meal-plan.jsx). Сознательное отличие от JS: количество округляется вверх
до 0.1 (ceil), а не Math.round, — чтобы не докупать.
"""

from collections.abc import Iterable, Sequence
from decimal import ROUND_CEILING, Decimal

from meal_planner.core.models import DayMeals, Recipe, ShoppingGroup, ShoppingItem

CATEGORY_ORDER = ("Белок", "Крупы", "Овощи", "Яйца и молочное", "Прочее")


def count_portions(recipes: Iterable[Recipe]) -> dict[Recipe, int]:
    """Сколько раз встречается каждый рецепт, в порядке первого появления."""
    counts: dict[Recipe, int] = {}
    for recipe in recipes:
        counts[recipe] = counts.get(recipe, 0) + 1
    return counts


def aggregate(entries: Iterable[tuple[Recipe, int]]) -> list[ShoppingItem]:
    # Суммы точные (Decimal): в float 0.1 * 3 = 0.30000000000000004, и ceil дал бы 0.4.
    totals: dict[tuple[str, str, str], Decimal] = {}
    for recipe, portions in entries:
        for ing in recipe.ingredients:
            key = (ing.name, ing.unit, ing.category)
            totals[key] = totals.get(key, Decimal(0)) + Decimal(str(ing.qty)) * portions
    items = [
        ShoppingItem(
            name=name,
            qty=float(total.quantize(Decimal("0.1"), rounding=ROUND_CEILING)),
            unit=unit,
            category=category,
        )
        for (name, unit, category), total in totals.items()
    ]
    # Для текущих имён совпадает с JS localeCompare(…, 'ru') — см. test_shopping.
    return sorted(items, key=lambda item: item.name)


def group_by_category(items: Iterable[ShoppingItem]) -> tuple[ShoppingGroup, ...]:
    groups: dict[str, list[ShoppingItem]] = {}
    for item in items:
        groups.setdefault(item.category, []).append(item)
    return tuple(
        ShoppingGroup(category=c, items=tuple(groups[c]))
        for c in CATEGORY_ORDER
        if c in groups
    )


def extract_shopping_list(days: Sequence[DayMeals]) -> tuple[ShoppingGroup, ...]:
    # JS ShoppingTab: сначала все обеды, потом ужины, потом завтраки.
    entries = [
        *count_portions(d.lunch for d in days).items(),
        *count_portions(d.dinner for d in days).items(),
        *count_portions(d.breakfast for d in days).items(),
    ]
    return group_by_category(aggregate(entries))


def format_qty(qty: float) -> str:
    return f"{qty:g}"
