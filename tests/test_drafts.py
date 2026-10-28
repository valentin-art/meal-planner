"""Черновики рецептов от Assist (8.6a): propose, confirm, discard.

Новое правило, а не порт JSX: ожидания записаны как правило. Без черновиков
каталог прежний — это держат golden-тесты.
"""

from dataclasses import replace
from datetime import date

import pytest

from meal_planner.core import catalog_actions as ca
from meal_planner.core.catalog import build_catalog
from meal_planner.core.catalog_actions import (
    CatalogError,
    ProposedLine,
    RecipeProposal,
)
from meal_planner.core.models import (
    Catalog,
    MealKind,
    RecipeSpec,
    RecipeStorage,
    Settings,
)
from meal_planner.core.nutrition import nutrition
from meal_planner.core.planner import next_shopping_trip
from meal_planner.core.recipes_data import RECIPE_SPECS
from meal_planner.core.scheduling import build_schedule
from meal_planner.core.seed import SEED_CATALOG, seed_ingredients, seed_slot_versions
from meal_planner.core.slot_pool import fish_slots

BASE = Settings(date(2026, 11, 5), 2, 3, 6, "same")
TODAY = date(2026, 11, 9)
STORAGE = RecipeStorage(
    "В контейнере, до 3 дней", "Уровень 4, 3 минуты", "800 Вт, 2 мин"
)


def proposal(**changes) -> RecipeProposal:
    base = RecipeProposal(
        name="Говядина с гречкой",
        kind=MealKind.DINNER,
        ingredients=(
            ProposedLine("  Говядина ", 180),
            ProposedLine("ГРЕЧКА", 80),
        ),
        steps=("Отварите гречку.", "Потушите говядину."),
        storage=STORAGE,
    )
    return replace(base, **changes)


def errors(call) -> list[tuple[tuple, str]]:
    with pytest.raises(CatalogError) as e:
        call()
    return [(err.loc, err.message) for err in e.value.errors]


def with_specs(*extra: RecipeSpec) -> Catalog:
    return build_catalog(
        seed_ingredients(), [*RECIPE_SPECS.values(), *extra], seed_slot_versions()
    )


DRAFT = ca.propose_recipe(SEED_CATALOG, proposal())
WITH_DRAFT = with_specs(DRAFT)


# --- propose ---


def test_proposal_becomes_draft_with_catalog_names():
    assert (DRAFT.id, DRAFT.status) == ("d6", "draft")
    assert [(i.name, i.qty, i.unit) for i in DRAFT.ingredients] == [
        ("говядина", 180, "г"),
        ("гречка", 80, "г"),
    ]
    # Ккал и белок — из справочника, модель их не передаёт.
    recipe = WITH_DRAFT.recipes["d6"]
    assert (recipe.kcal, recipe.protein) == nutrition(
        SEED_CATALOG.ingredients, DRAFT.ingredients
    )


def test_unknown_ingredient_is_named():
    lines = (ProposedLine("говядина", 180), ProposedLine("тофу", 0))
    assert errors(
        lambda: ca.propose_recipe(SEED_CATALOG, proposal(ingredients=lines, name=""))
    ) == [
        (
            ("ingredients", 1, "name"),
            "«тофу» нет в справочнике — добавьте его в приложении или замените",
        ),
        (("name",), "Название не может быть пустым"),
    ]


def test_other_errors_are_those_of_save_recipe():
    lines = (ProposedLine("говядина", 0), ProposedLine("Говядина", 10))
    assert errors(
        lambda: ca.propose_recipe(SEED_CATALOG, proposal(ingredients=lines))
    ) == [
        (("ingredients", 0, "qty"), "Количество должно быть больше нуля"),
        (("ingredients", 1, "ingredient_id"), "«говядина» уже есть в рецепте"),
    ]


def test_breakfast_is_refused():
    assert errors(
        lambda: ca.propose_recipe(
            SEED_CATALOG, proposal(kind=MealKind.BREAKFAST, storage=None)
        )
    ) == [(("kind",), "Assist предлагает только обеды и ужины")]


# --- черновик не в плане ---


def test_draft_is_not_placed_and_not_in_plan():
    assert ca.plan_for(SEED_CATALOG, DRAFT, 2).slot_versions == (
        SEED_CATALOG.slot_versions
    )
    window = date(2026, 11, 1), date(2027, 1, 31)
    assert build_schedule(WITH_DRAFT, BASE, *window) == build_schedule(
        SEED_CATALOG, BASE, *window
    )
    assert next_shopping_trip(WITH_DRAFT, BASE, TODAY) == next_shopping_trip(
        SEED_CATALOG, BASE, TODAY
    )


def test_draft_cannot_go_to_slot_archive_or_restore():
    assert errors(
        lambda: ca.set_cycle_slot(WITH_DRAFT, 1, ("l1",), ("d1", "d6"), 2)
    ) == [
        (
            ("dinner_ids", 1),
            "«Говядина с гречкой» — черновик, сначала подтвердите",
        )
    ]
    message = "«Говядина с гречкой» — черновик: подтвердите или отбросьте его"
    assert errors(lambda: ca.archive_recipe(WITH_DRAFT, "d6")) == [(("id",), message)]
    assert errors(lambda: ca.restore_recipe(WITH_DRAFT, "d6")) == [(("id",), message)]


def test_edit_keeps_draft():
    edited = ca.save_recipe(
        WITH_DRAFT,
        ca.RecipeInput(
            id="d6",
            name="Говядина с гречкой и луком",
            kind=MealKind.DINNER,
            ingredients=tuple(
                ca.RecipeLineInput(SEED_CATALOG.ingredients[n].id, q)
                for n, q in (("говядина", 180), ("лук репчатый", 50))
            ),
            steps=("Потушите.",),
            storage=STORAGE,
        ),
    )
    assert edited.status == "draft"
    assert ca.plan_for(WITH_DRAFT, edited, 2).slot_versions == (
        WITH_DRAFT.slot_versions
    )


# --- confirm / discard ---


def test_confirm_places_like_new_recipe():
    confirmed = ca.confirm_draft(WITH_DRAFT, "d6")
    assert confirmed.status == "active"
    versions = ca.plan_for(WITH_DRAFT, confirmed, 2).slot_versions
    new = [v for v in versions if v not in WITH_DRAFT.slot_versions]
    # Как новый ужин: позиция с меньшим числом вариантов, с первого
    # некупленного цикла.
    assert [(v.slot, v.from_cycle, v.dinner_ids) for v in new] == [(1, 2, ("d1", "d6"))]


def test_confirmed_fish_goes_only_to_fish_slot():
    fish = ca.propose_recipe(
        SEED_CATALOG,
        proposal(
            name="Лосось с рисом",
            ingredients=(ProposedLine("лосось", 150), ProposedLine("рис басмати", 70)),
            is_fish=True,
        ),
    )
    catalog = with_specs(fish)
    is_fish = lambda rid: catalog.recipes[rid].is_fish
    slots = fish_slots(catalog.slot_versions, 0, is_fish)
    versions = ca.plan_for(catalog, ca.confirm_draft(catalog, fish.id), 2).slot_versions
    (new,) = [v for v in versions if v not in catalog.slot_versions]
    assert new.slot in slots and fish.id in new.dinner_ids


@pytest.mark.parametrize("action", [ca.confirm_draft, ca.discard_draft])
def test_confirm_and_discard_need_a_draft(action):
    name = WITH_DRAFT.recipes["d1"].name
    assert errors(lambda: action(WITH_DRAFT, "d1")) == [
        (("id",), f"«{name}» — не черновик")
    ]
    assert errors(lambda: action(WITH_DRAFT, "d9")) == [(("id",), "Рецепта d9 нет")]


def test_discard_returns_id():
    assert ca.discard_draft(WITH_DRAFT, "d6") == "d6"
