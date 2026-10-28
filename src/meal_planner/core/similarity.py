"""Похож ли рецепт от Assist на уже существующие (8.6c).

Сходство — доля общего веса: по каждому общему ингредиенту берётся меньшая
из его долей в двух рецептах (по граммам), доли складываются. Главные
продукты весят больше ложки масла. На сид-данных два салата с курицей (d1,
d4) — 0.73, курица с киноа и с булгуром (l1, l4) — 0.59: с порога 0.6
похожим считается почти то же блюдо, а не та же курица с другой основой.
"""

from dataclasses import dataclass
from decimal import Decimal

from meal_planner.core.models import Catalog, MealKind, Recipe, RecipeSpec
from meal_planner.core.nutrition import grams

SIMILAR_FROM = Decimal("0.6")
# С чем сравнивать по составу: только пул. Черновики между собой могут быть
# похожи (варианты одной идеи, правка черновика) — их разбирает пользователь;
# архив — то, что надоело, к похожему можно вернуться. Название сверяется и с
# черновиками (catalog_actions).
COMPARED_STATUSES = ("active",)


@dataclass(frozen=True, slots=True)
class Similar:
    recipe: Recipe
    score: Decimal
    common: tuple[str, ...]  # общие ингредиенты, по убыванию веса, затем по имени


def weight_shares(catalog: Catalog, recipe: Recipe | RecipeSpec) -> dict[str, Decimal]:
    """Доля каждого ингредиента в весе рецепта (ложки и штуки — по весу)."""
    weights = {i.name: grams(catalog.ingredients, i) for i in recipe.ingredients}
    total = sum(weights.values(), Decimal(0))
    if not total:
        return {}
    return {name: w / total for name, w in weights.items()}


def similarity(
    catalog: Catalog, a: Recipe | RecipeSpec, b: Recipe | RecipeSpec
) -> Decimal:
    shares_a, shares_b = weight_shares(catalog, a), weight_shares(catalog, b)
    return sum(
        (
            min(shares_a[name], shares_b[name])
            # sorted — порядок сложения не зависит от хеша строк
            for name in sorted(shares_a.keys() & shares_b.keys())
        ),
        Decimal(0),
    )


def similar_recipe(catalog: Catalog, spec: RecipeSpec) -> Similar | None:
    """Самый похожий обед или ужин из пула, если сходство ≥ порога."""
    best: Similar | None = None
    shares = weight_shares(catalog, spec)
    for recipe in catalog.recipes.values():
        if (
            recipe.id == spec.id
            or recipe.kind == MealKind.BREAKFAST
            or recipe.status not in COMPARED_STATUSES
        ):
            continue
        score = similarity(catalog, spec, recipe)
        if score >= SIMILAR_FROM and (best is None or score > best.score):
            common = sorted(
                shares.keys() & {i.name for i in recipe.ingredients},
                key=lambda name: (-shares[name], name),
            )
            best = Similar(recipe, score, tuple(common))
    return best
