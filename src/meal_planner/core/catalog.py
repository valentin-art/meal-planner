"""Каталог (5.3): рецепты с ккал по справочнику и состав позиций цикла.

Данные приходят параметром — из БД (API) или сида (тесты); глобальных
справочников в core/ нет.
"""

from collections.abc import Iterable
from types import MappingProxyType

from meal_planner.core.models import (
    Catalog,
    CatalogIngredient,
    MealKind,
    Recipe,
    RecipeSpec,
    SlotVersion,
)
from meal_planner.core.nutrition import nutrition

KIND_ORDER = tuple(MealKind)  # завтрак, обед, ужин


def catalog_order(spec: RecipeSpec) -> tuple[int, int]:
    # Номер — числом: l10 после l9. Id выдаёт сервер — префикс типа и номер (5.1).
    return KIND_ORDER.index(spec.kind), int(spec.id[1:])


def spec_of(recipe: Recipe) -> RecipeSpec:
    """Рецепт каталога → как хранится: без ккал и белка."""
    return RecipeSpec(
        id=recipe.id,
        name=recipe.name,
        kind=recipe.kind,
        ingredients=recipe.ingredients,
        steps=recipe.steps,
        storage=recipe.storage,
        is_fish=recipe.is_fish,
        status=recipe.status,
    )


def build_catalog(
    ingredients: Iterable[CatalogIngredient],
    recipes: Iterable[RecipeSpec],
    slot_versions: Iterable[SlotVersion],
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
            status=spec.status,
        )
    return Catalog(
        recipes=MappingProxyType(built),
        ingredients=MappingProxyType(by_name),
        slot_versions=tuple(
            sorted(slot_versions, key=lambda v: (v.slot, v.from_cycle))
        ),
    )
