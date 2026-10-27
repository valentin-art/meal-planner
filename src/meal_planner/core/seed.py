"""Сид каталога (5.2): справочник ингредиентов и слоты цикла из констант.

recipes_data.py, nutrition.py и порядок ротации в scheduling.py остаются
источником: из них один раз заполняется БД (db/schema.py), на них же
держатся golden.
"""

from meal_planner.core.models import CatalogIngredient, CycleSlot, Ingredient
from meal_planner.core.nutrition import GRAMS_PER_UNIT, PER_100G
from meal_planner.core.recipes_data import ALL_RECIPES, STORE_PIECES
from meal_planner.core.scheduling import DINNER_IDS, LUNCH_IDS


def seed_ingredients() -> tuple[CatalogIngredient, ...]:
    """Справочник в порядке первого появления в рецептах."""
    # Единица и категория — из рецептов: у каждого ингредиента они одни (5.1).
    found: dict[str, Ingredient] = {}
    for recipe in ALL_RECIPES.values():
        for ingredient in recipe.ingredients:
            first = found.setdefault(ingredient.name, ingredient)
            if (first.unit, first.category) != (ingredient.unit, ingredient.category):
                raise ValueError(
                    f"{ingredient.name}: разные единицы или категории в рецептах"
                )
    result = []
    for name, ingredient in found.items():
        kcal, protein, source = PER_100G[name]
        key = (name, ingredient.unit)
        result.append(
            CatalogIngredient(
                name=name,
                unit=ingredient.unit,
                category=ingredient.category,
                kcal_100g=kcal,
                protein_100g=protein,
                source=source,
                grams_per_unit=GRAMS_PER_UNIT.get(key),
                store_piece=STORE_PIECES.get(key),
            )
        )
    return tuple(result)


def seed_slots() -> tuple[CycleSlot, ...]:
    return tuple(
        CycleSlot(slot=k, lunch_id=lunch, dinner_id=dinner)
        for k, (lunch, dinner) in enumerate(zip(LUNCH_IDS, DINNER_IDS), start=1)
    )
