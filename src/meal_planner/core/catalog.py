"""Каталог (5.3): рецепты с ккал по справочнику и слоты цикла.

Данные приходят параметром — из БД (API) или сида (тесты); глобальных
справочников в core/ нет.
"""

from collections.abc import Iterable
from types import MappingProxyType

from meal_planner.core.models import (
    Catalog,
    CatalogIngredient,
    CycleSlot,
    MealKind,
    Recipe,
    RecipeSpec,
)
from meal_planner.core.nutrition import nutrition

KIND_ORDER = tuple(MealKind)  # завтрак, обед, ужин


def catalog_order(spec: RecipeSpec) -> tuple[int, int]:
    # Номер — числом: l10 после l9. Id выдаёт сервер — префикс типа и номер (5.1).
    return KIND_ORDER.index(spec.kind), int(spec.id[1:])


def build_catalog(
    ingredients: Iterable[CatalogIngredient],
    recipes: Iterable[RecipeSpec],
    slots: Iterable[CycleSlot],
) -> Catalog:
    by_name = {i.name: i for i in ingredients}
    built = {}
    for spec in sorted(recipes, key=catalog_order):
        # Ккал и белок — из ингредиентов (6.9), по справочнику каталога.
        kcal, protein = nutrition(by_name, spec.ingredients)
        built[spec.id] = Recipe(
            id=spec.id,
            name=spec.name,
            kind=spec.kind,
            kcal=kcal,
            protein=protein,
            ingredients=spec.ingredients,
            steps=spec.steps,
            storage=spec.storage,
            is_fish=spec.is_fish,
        )
    return Catalog(
        recipes=MappingProxyType(built),
        ingredients=MappingProxyType(by_name),
        slots=tuple(sorted(slots, key=lambda s: s.slot)),
    )
