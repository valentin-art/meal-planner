"""Черновики рецептов от Assist (8.6a): propose, confirm, discard.

С 8.6b рецепт может принести новые ингредиенты, а количества core переводит
в единицу справочника.

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
    NewIngredient,
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
        name="Говядина с булгуром",
        kind=MealKind.DINNER,
        ingredients=(
            ProposedLine("  Говядина ", 150, "г"),
            ProposedLine("БУЛГУР", 70, "г"),
            ProposedLine("баклажан", 150, "г"),
        ),
        steps=("Отварите булгур.", "Потушите говядину с баклажаном."),
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


DRAFT = ca.propose_recipe(SEED_CATALOG, proposal()).recipe
WITH_DRAFT = with_specs(DRAFT)


# --- propose ---


def test_proposal_becomes_draft_with_catalog_names():
    assert (DRAFT.id, DRAFT.status) == ("d6", "draft")
    assert [(i.name, i.qty, i.unit) for i in DRAFT.ingredients] == [
        ("говядина", 150, "г"),
        ("булгур", 70, "г"),
        ("баклажан", 150, "г"),
    ]
    # Ккал и белок — из справочника, модель их не передаёт.
    recipe = WITH_DRAFT.recipes["d6"]
    assert (recipe.kcal, recipe.protein) == nutrition(
        SEED_CATALOG.ingredients, DRAFT.ingredients
    )


def test_unknown_ingredient_is_named():
    lines = (ProposedLine("говядина", 180, "г"), ProposedLine("тофу", 0, "г"))
    assert errors(
        lambda: ca.propose_recipe(SEED_CATALOG, proposal(ingredients=lines, name=""))
    ) == [
        (
            ("ingredients", 1, "name"),
            "«тофу» нет в справочнике — опишите его в new_ingredients",
        ),
        (("name",), "Название не может быть пустым"),
    ]


def test_other_errors_are_those_of_save_recipe():
    lines = (ProposedLine("говядина", 0, "г"), ProposedLine("Говядина", 10, "г"))
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
            "«Говядина с булгуром» — черновик, сначала подтвердите",
        )
    ]
    message = "«Говядина с булгуром» — черновик: подтвердите или отбросьте его"
    assert errors(lambda: ca.archive_recipe(WITH_DRAFT, "d6")) == [(("id",), message)]
    assert errors(lambda: ca.restore_recipe(WITH_DRAFT, "d6")) == [(("id",), message)]


def test_edit_keeps_draft():
    edited = ca.save_recipe(
        WITH_DRAFT,
        ca.RecipeInput(
            id="d6",
            name="Говядина с булгуром и луком",
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
            ingredients=(
                ProposedLine("лосось", 150, "г"),
                ProposedLine("булгур", 70, "г"),
                ProposedLine("баклажан", 150, "г"),
            ),
            is_fish=True,
        ),
    ).recipe
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


# --- новые ингредиенты и единицы (8.6b) ---

TOFU = NewIngredient("Тофу", "г", "Белок", 76, 8.08, source="fdc 172475")
SOY = NewIngredient("соевый соус", "ст.л.", "Прочее", 53, 8.14, grams_per_unit=16)


def test_new_ingredients_join_catalog_with_the_draft():
    lines = (
        ProposedLine("тофу", 0.2, "кг"),
        ProposedLine("Соевый соус", 1.5, "ст.л."),
        ProposedLine("гречка", 80, "г"),
    )
    proposed = ca.propose_recipe(
        SEED_CATALOG, proposal(ingredients=lines, new_ingredients=(TOFU, SOY))
    )
    next_id = max(i.id for i in SEED_CATALOG.ingredients.values()) + 1
    assert [(i.id, i.name, i.unit, i.source) for i in proposed.ingredients] == [
        (next_id, "Тофу", "г", "Assist: fdc 172475"),
        (next_id + 1, "соевый соус", "ст.л.", "Assist: оценка модели"),
    ]
    assert all(ca.from_assist(i) for i in proposed.ingredients)
    assert not ca.from_assist(SEED_CATALOG.ingredients["гречка"])
    assert [(i.name, i.qty, i.unit) for i in proposed.recipe.ingredients] == [
        ("Тофу", 200, "г"),
        ("соевый соус", 1.5, "ст.л."),
        ("гречка", 80, "г"),
    ]
    catalog = build_catalog(
        [*seed_ingredients(), *proposed.ingredients],
        [*RECIPE_SPECS.values(), proposed.recipe],
        seed_slot_versions(),
    )
    # 200 г × 0.76 + 1.5 × 16 г × 0.53 + 80 г × 3.46 = 152 + 12.7 + 276.8 → 440
    assert catalog.recipes["d6"].kcal == 440


def test_known_name_in_new_ingredients_takes_the_catalog_one():
    known = NewIngredient("Булгур", "мл", "Крупы", 1, 1)
    proposed = ca.propose_recipe(SEED_CATALOG, proposal(new_ingredients=(known,)))
    assert proposed.ingredients == ()
    assert proposed.recipe.ingredients[1].name == "булгур"


def test_new_ingredient_rules():
    piece = NewIngredient("кабачковая икра", "шт", "Овощи", 90, 1.2)
    spoon = NewIngredient("паприка", "ч.л.", "Прочее", 282, 14.1)
    lines = (
        ProposedLine("говядина", 180, "г"),
        ProposedLine("паприка", 0.5, "ч.л."),
    )
    assert errors(
        lambda: ca.propose_recipe(
            SEED_CATALOG,
            proposal(ingredients=lines, new_ingredients=(piece, spoon, TOFU, TOFU)),
        )
    ) == [
        (
            ("new_ingredients", 0, "unit"),
            "Единица нового ингредиента — одна из: г, мл, ч.л., ст.л.",
        ),
        (
            ("new_ingredients", 1, "grams_per_unit"),
            "Сколько граммов в «ч.л.» — нужно число больше нуля",
        ),
        (("new_ingredients", 3, "name"), "«Тофу» уже описан"),
    ]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (ProposedLine("говядина", 0.25, "кг"), ("говядина", 250, "г")),
        (ProposedLine("цукини", 1, "шт"), ("цукини", 280, "г")),  # store_piece
        (ProposedLine("сок лимона", 1, "ч.л."), ("сок лимона", 5, "мл")),
        (
            ProposedLine("оливковое масло", 1, "ч.л."),
            ("оливковое масло", 0.25, "ст.л."),
        ),
        (ProposedLine("оливковое масло", 20, "г"), ("оливковое масло", 1.5, "ст.л.")),
        (ProposedLine("чеснок", 2, "зубчик"), ("чеснок", 2, "зубчик")),
        (ProposedLine("говядина", 180.4, "г"), ("говядина", 180, "г")),
    ],
)
def test_line_is_converted_to_catalog_unit(line, expected):
    lines = (line, ProposedLine("баклажан", 150, "г"))
    recipe = ca.propose_recipe(SEED_CATALOG, proposal(ingredients=lines)).recipe
    first = recipe.ingredients[0]
    assert (first.name, first.qty, first.unit) == expected


def test_unconvertible_line_is_refused():
    lines = (
        ProposedLine("говядина", 1, "ч.л."),
        ProposedLine("гречка", 1, "щепотка"),
        ProposedLine("брокколи", 1, "шт"),
    )
    assert errors(
        lambda: ca.propose_recipe(SEED_CATALOG, proposal(ingredients=lines))
    ) == [
        (("ingredients", 0, "unit"), "«говядина»: не перевести ч.л. в г — укажите в г"),
        (
            ("ingredients", 1, "unit"),
            (
                "«гречка»: единица — одна из: г, кг, мл, л, стакан, ч.л., ст.л., шт,"
                " зубчик"
            ),
        ),
        (
            ("ingredients", 2, "unit"),
            (
                "«брокколи»: не перевести шт в г — у ингредиента нет веса штуки"
                " — укажите в г"
            ),
        ),
    ]


def test_line_of_rejected_new_ingredient_has_no_second_error():
    piece = NewIngredient("кабачковая икра", "шт", "Овощи", 90, 1.2)
    lines = (ProposedLine("кабачковая икра", 1, "шт"),)
    assert [
        loc
        for loc, _ in errors(
            lambda: ca.propose_recipe(
                SEED_CATALOG, proposal(ingredients=lines, new_ingredients=(piece,))
            )
        )
    ] == [("new_ingredients", 0, "unit")]


# --- не повторяет существующие (8.6c) ---


def test_similar_composition_is_refused():
    # l2 «Гречка с тушёной говядиной»: та же говядина с гречкой.
    lines = (ProposedLine("говядина", 180, "г"), ProposedLine("гречка", 80, "г"))
    assert errors(
        lambda: ca.propose_recipe(SEED_CATALOG, proposal(ingredients=lines))
    ) == [
        (
            ("ingredients",),
            (
                f"Похоже на «{SEED_CATALOG.recipes['l2'].name}» (общее: говядина, гречка)"
                " — придумай другое блюдо"
            ),
        )
    ]


def test_same_name_is_refused():
    name = SEED_CATALOG.recipes["d3"].name
    assert errors(
        lambda: ca.propose_recipe(SEED_CATALOG, proposal(name=f" {name.upper()} "))
    ) == [(("name",), f"«{name}» уже есть — придумай другое блюдо")]


def test_draft_counts_by_name_only_archive_not_at_all():
    # Тот же рецепт вторым черновиком: название занято, а состав — можно.
    assert [
        loc for loc, _ in errors(lambda: ca.propose_recipe(WITH_DRAFT, proposal()))
    ] == [("name",)]
    variant = ca.propose_recipe(WITH_DRAFT, proposal(name="Говядина с булгуром 2"))
    assert (variant.recipe.id, variant.recipe.status) == ("d7", "draft")
    archived = with_specs(replace(DRAFT, status="archived"))
    assert ca.propose_recipe(archived, proposal()).recipe.id == "d7"


# --- правка черновика и проверка при «В план» (8.6c) ---


def test_update_draft_keeps_id_and_status():
    lines = (
        ProposedLine("говядина", 150, "г"),
        ProposedLine("булгур", 70, "г"),
        ProposedLine("цукини", 100, "г"),
    )
    new = ca.propose_recipe(
        WITH_DRAFT, proposal(name="Говядина с цукини", ingredients=lines), "d6"
    )
    assert (new.recipe.id, new.recipe.status) == ("d6", "draft")
    assert [i.name for i in new.recipe.ingredients] == ["говядина", "булгур", "цукини"]
    # Своё название и свой состав не мешают: сверка без самого себя.
    same = ca.propose_recipe(WITH_DRAFT, proposal(), "d6")
    assert same.recipe.id == "d6"


def test_update_refuses_non_draft_and_other_kind():
    name = SEED_CATALOG.recipes["d1"].name
    assert errors(lambda: ca.propose_recipe(WITH_DRAFT, proposal(), "d1")) == [
        (("id",), f"«{name}» — не черновик")
    ]
    assert errors(lambda: ca.propose_recipe(WITH_DRAFT, proposal(), "d9")) == [
        (("id",), "Рецепта d9 нет")
    ]
    assert errors(
        lambda: ca.propose_recipe(WITH_DRAFT, proposal(kind=MealKind.LUNCH), "d6")
    ) == [(("kind",), "Тип блюда не меняется — создайте новый рецепт")]


def test_update_still_refuses_similar_to_pool():
    lines = (ProposedLine("говядина", 180, "г"), ProposedLine("гречка", 80, "г"))
    assert [
        loc
        for loc, _ in errors(
            lambda: ca.propose_recipe(WITH_DRAFT, proposal(ingredients=lines), "d6")
        )
    ] == [("ingredients",)]


def test_confirm_checks_similarity_to_pool_again():
    # Пока черновик лежал, в пул попало похожее — «В план» отказывает.
    sibling = ca.propose_recipe(WITH_DRAFT, proposal(name="Говядина с булгуром 2"))
    both = with_specs(DRAFT, ca.confirm_draft(WITH_DRAFT, "d6"), sibling.recipe)
    assert both.recipes["d6"].status == "active"
    ((loc, message),) = errors(lambda: ca.confirm_draft(both, "d7"))
    assert loc == ("id",)
    assert message.startswith("«Говядина с булгуром 2» похоже на «Говядина с булгуром»")
    assert message.endswith("измените черновик или отбросьте его")
