"""Сходство рецептов по доле общего веса (8.6c)."""

from dataclasses import replace
from decimal import Decimal

from meal_planner.core.catalog import build_catalog, spec_of
from meal_planner.core.models import Ingredient
from meal_planner.core.recipes_data import RECIPE_SPECS
from meal_planner.core.seed import SEED_CATALOG, seed_ingredients, seed_slot_versions
from meal_planner.core.similarity import (
    SIMILAR_FROM,
    similar_recipe,
    similarity,
    weight_shares,
)

R = SEED_CATALOG.recipes


def test_seed_pairs_around_threshold():
    # Два салата с курицей — почти то же блюдо; курица с другой крупой — нет.
    assert round(similarity(SEED_CATALOG, R["d1"], R["d4"]), 2) == Decimal("0.73")
    assert round(similarity(SEED_CATALOG, R["l1"], R["l4"]), 2) == Decimal("0.59")
    assert similarity(SEED_CATALOG, R["l1"], R["l4"]) < SIMILAR_FROM
    assert round(similarity(SEED_CATALOG, R["d3"], R["d3"]), 9) == 1


def test_spoon_counts_by_its_weight():
    # 1 ст.л. масла — 13.6 г, а не 1.
    line = Ingredient("масло растительное", 1, "ст.л.", "Прочее")
    beef = Ingredient("говядина", 136, "г", "Белок")
    spec = replace(spec_of(R["l2"]), ingredients=(beef, line))
    shares = weight_shares(SEED_CATALOG, spec)
    assert shares["масло растительное"] == Decimal("13.6") / Decimal("149.6")
    assert round(sum(shares.values()), 9) == 1


def test_similar_recipe_takes_the_most_similar_and_skips_archive():
    copy = replace(spec_of(R["d4"]), id="d6", name="Копия")
    found = similar_recipe(SEED_CATALOG, copy)
    assert found is not None
    assert (found.recipe.id, round(found.score, 9)) == ("d4", 1)
    # По убыванию веса; курица и помидор по 160 г — по имени.
    assert found.common[:3] == ("куриное филе", "помидор", "огурец")
    # d4 в архиве — остаётся только d1 (0.73).
    archived = build_catalog(
        seed_ingredients(),
        [*RECIPE_SPECS.values(), replace(RECIPE_SPECS["d4"], status="archived")],
        seed_slot_versions(),
    )
    found = similar_recipe(archived, copy)
    assert found is not None and found.recipe.id == "d1"


def test_draft_is_compared():
    draft = replace(spec_of(R["l2"]), id="l6", name="Черновик", status="draft")
    catalog = build_catalog(
        seed_ingredients(), [*RECIPE_SPECS.values(), draft], seed_slot_versions()
    )
    again = replace(draft, id="l7")
    found = similar_recipe(catalog, again)
    assert found is not None and round(found.score, 9) == 1
