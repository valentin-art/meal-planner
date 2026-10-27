"""Сид каталога (5.2): справочник ингредиентов, рецепты и слоты из констант.

recipes_data.py, nutrition.py и порядок ротации ниже остаются источником:
из них один раз заполняется БД (db/schema.py), на SEED_CATALOG держатся
golden и тесты core/ (5.3).
"""

from meal_planner.core.catalog import build_catalog
from meal_planner.core.models import CatalogIngredient, CycleSlot, Ingredient
from meal_planner.core.nutrition import GRAMS_PER_UNIT, PER_100G
from meal_planner.core.recipes_data import RECIPE_SPECS, STORE_PIECES

# Порядок 5-дневной ротации: позиция k (1..5) → обед/ужин LUNCH_IDS[k-1]/DINNER_IDS[k-1].
LUNCH_IDS = ("l1", "l2", "l3", "l4", "l5")
DINNER_IDS = ("d1", "d2", "d3", "d4", "d5")


def seed_ingredients() -> tuple[CatalogIngredient, ...]:
    """Справочник в порядке первого появления в рецептах; id — 1..N, как
    autoincrement при заполнении пустой таблицы."""
    # Единица и категория — из рецептов: у каждого ингредиента они одни (5.1).
    found: dict[str, Ingredient] = {}
    for recipe in RECIPE_SPECS.values():
        for ingredient in recipe.ingredients:
            first = found.setdefault(ingredient.name, ingredient)
            if (first.unit, first.category) != (ingredient.unit, ingredient.category):
                raise ValueError(
                    f"{ingredient.name}: разные единицы или категории в рецептах"
                )
    result = []
    for i, (name, ingredient) in enumerate(found.items(), start=1):
        kcal, protein, source = PER_100G[name]
        key = (name, ingredient.unit)
        result.append(
            CatalogIngredient(
                id=i,
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


SEED_CATALOG = build_catalog(seed_ingredients(), RECIPE_SPECS.values(), seed_slots())
