"""Список покупок: подсчёт порций, агрегация ингредиентов, группировка.

Порт aggregate/groupByCategory/fmtQty и подсчёта порций из ShoppingTab
(meal-plan.jsx). Сознательное отличие от JS: количество округляется вверх
до 0.1 (ceil), а не Math.round, — чтобы не докупать.
"""

from collections.abc import Iterable, Sequence
from decimal import ROUND_CEILING, Decimal

from meal_planner.core.models import (
    Catalog,
    CatalogIngredient,
    DayMeals,
    Recipe,
    ShoppingGroup,
    ShoppingItem,
)

CATEGORY_ORDER = ("Белок", "Крупы", "Овощи", "Яйца и молочное", "Прочее")


def count_portions(recipes: Iterable[Recipe | None]) -> dict[Recipe, int]:
    """Сколько раз встречается каждый рецепт, в порядке первого появления.

    None — день без блюд (до старта цикла и день 0, 6.5) — пропускается.
    """
    counts: dict[Recipe, int] = {}
    for recipe in recipes:
        if recipe is not None:
            counts[recipe] = counts.get(recipe, 0) + 1
    return counts


def aggregate(
    catalog: Catalog, entries: Iterable[tuple[Recipe, int]]
) -> list[ShoppingItem]:
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
            pieces=store_pieces(catalog.ingredients[name], total),
        )
        for (name, unit, category), total in totals.items()
    ]
    # Для текущих имён совпадает с JS localeCompare(…, 'ru') — см. test_shopping.
    return sorted(items, key=lambda item: item.name)


def store_pieces(ingredient: CatalogIngredient, total: Decimal) -> int | None:
    # Сколько штук купить (6.7): вверх — часть штуки в магазине не купишь.
    # Вес штуки — в единице ингредиента, она у ингредиента одна (5.1).
    per_piece = ingredient.store_piece
    if per_piece is None:
        return None
    return int((total / Decimal(str(per_piece))).to_integral_value(ROUND_CEILING))


def group_by_category(items: Iterable[ShoppingItem]) -> tuple[ShoppingGroup, ...]:
    groups: dict[str, list[ShoppingItem]] = {}
    for item in items:
        groups.setdefault(item.category, []).append(item)
    return tuple(
        ShoppingGroup(category=c, items=tuple(groups[c]))
        for c in CATEGORY_ORDER
        if c in groups
    )


def extract_shopping_list(
    catalog: Catalog, days: Sequence[DayMeals]
) -> tuple[ShoppingGroup, ...]:
    # JS ShoppingTab: сначала все обеды, потом ужины, потом завтраки.
    # Дни без блюд (до старта цикла, 6.5) count_portions пропускает.
    entries = [
        *count_portions(d.lunch for d in days).items(),
        *count_portions(d.dinner for d in days).items(),
        *count_portions(d.breakfast for d in days).items(),
    ]
    return group_by_category(aggregate(catalog, entries))


def format_qty(qty: float) -> str:
    return f"{qty:g}"
