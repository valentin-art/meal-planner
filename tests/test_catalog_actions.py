"""5.4: правила правки каталога (core/catalog_actions) и признак plan_use."""

from dataclasses import replace
from datetime import date, timedelta

import pytest
from golden_scheduling import SCENARIOS
from scenario_settings import settings_of

from meal_planner.core import catalog_actions as ca
from meal_planner.core.catalog import build_catalog, spec_of
from meal_planner.core.catalog_actions import (
    CatalogError,
    IngredientInput,
    RecipeInput,
    RecipeLineInput,
)
from meal_planner.core.models import MealKind, RecipeStorage
from meal_planner.core.planner import next_shopping_trip, recompute
from meal_planner.core.recipes_data import RECIPE_SPECS
from meal_planner.core.scheduling import build_schedule
from meal_planner.core.seed import SEED_CATALOG, seed_ingredients, seed_slots

DEFAULT_2X = settings_of(next(s for s in SCENARIOS if s["name"] == "default_2x"))
ID = {i.name: i.id for i in SEED_CATALOG.ingredients.values()}
STORAGE = RecipeStorage(
    "В контейнере, до 3 дней", "Уровень 4, 3 минуты", "800 Вт, 2 мин"
)


def lunch(**changes) -> RecipeInput:
    base = RecipeInput(
        id=None,
        name="Курица с булгуром",
        kind=MealKind.LUNCH,
        ingredients=(
            RecipeLineInput(ID["куриное филе"], 180),
            RecipeLineInput(ID["булгур"], 80),
        ),
        steps=("Отварите булгур.", "Обжарьте курицу."),
        storage=STORAGE,
    )
    return replace(base, **changes)


def errors(call) -> list[tuple[tuple, str]]:
    with pytest.raises(CatalogError) as e:
        call()
    return [(err.loc, err.message) for err in e.value.errors]


def with_specs(*extra):
    return build_catalog(
        seed_ingredients(), [*RECIPE_SPECS.values(), *extra], seed_slots()
    )


# --- save-recipe ---


def test_new_recipe_gets_next_id_and_catalog_units():
    spec = ca.save_recipe(SEED_CATALOG, lunch())
    assert spec.id == "l6"
    assert spec.status == "active"
    assert [(i.name, i.qty, i.unit, i.category) for i in spec.ingredients] == [
        ("куриное филе", 180, "г", "Белок"),
        ("булгур", 80, "г", "Крупы"),
    ]
    # Ккал считает build_catalog, не клиент.
    catalog = with_specs(spec)
    assert catalog.recipes["l6"].kcal > 0


def test_new_breakfast_id_after_b3():
    breakfast = lunch(kind=MealKind.BREAKFAST, storage=None)
    assert ca.save_recipe(SEED_CATALOG, breakfast).id == "b4"


def test_ids_are_not_reused_after_archive():
    archived = replace(RECIPE_SPECS["l1"], id="l7", status="archived")
    assert ca.save_recipe(with_specs(archived), lunch()).id == "l8"


def test_edit_keeps_id_and_status():
    archived = replace(RECIPE_SPECS["l1"], id="l7", status="archived")
    spec = ca.save_recipe(with_specs(archived), lunch(id="l7", name="  Новое  "))
    assert (spec.id, spec.name, spec.status) == ("l7", "Новое", "archived")


def test_unknown_recipe_id():
    assert errors(lambda: ca.save_recipe(SEED_CATALOG, lunch(id="l99"))) == [
        (("id",), "Рецепта l99 нет")
    ]


def test_kind_does_not_change():
    assert errors(
        lambda: ca.save_recipe(SEED_CATALOG, lunch(id="d1", kind=MealKind.LUNCH))
    ) == [(("kind",), "Тип блюда не меняется — создайте новый рецепт")]


def test_all_recipe_errors_at_once():
    bad = lunch(
        name="  ",
        ingredients=(
            RecipeLineInput(9999, 10),
            RecipeLineInput(ID["булгур"], 0),
            RecipeLineInput(ID["булгур"], 50),
        ),
        steps=("Шаг", " "),
        storage=replace(STORAGE, pan=" "),
    )
    assert errors(lambda: ca.save_recipe(SEED_CATALOG, bad)) == [
        (("name",), "Название не может быть пустым"),
        (
            ("ingredients", 0, "ingredient_id"),
            "Такого ингредиента нет в справочнике",
        ),
        (("ingredients", 1, "qty"), "Количество должно быть больше нуля"),
        (("ingredients", 2, "ingredient_id"), "«булгур» уже есть в рецепте"),
        (("steps", 1), "Шаг не может быть пустым"),
        (("storage", "pan"), "Разогрев на сковороде: заполните поле"),
    ]


def test_empty_recipe():
    assert errors(
        lambda: ca.save_recipe(SEED_CATALOG, lunch(ingredients=(), steps=()))
    ) == [
        (("ingredients",), "Нужен хотя бы один ингредиент"),
        (("steps",), "Нужен хотя бы один шаг"),
    ]


def test_storage_by_kind():
    assert errors(lambda: ca.save_recipe(SEED_CATALOG, lunch(storage=None))) == [
        (
            ("storage",),
            "У обеда и ужина нужно хранение и разогрев на сковороде и в микроволновке",
        )
    ]
    breakfast = lunch(kind=MealKind.BREAKFAST)
    assert errors(lambda: ca.save_recipe(SEED_CATALOG, breakfast)) == [
        (("storage",), "У завтрака хранения нет")
    ]


def test_empty_after_is_none():
    spec = ca.save_recipe(SEED_CATALOG, lunch(storage=replace(STORAGE, after=" ")))
    assert spec.storage == replace(STORAGE, after=None)


# --- archive / restore ---


def test_archive_and_restore_unscheduled():
    archived = ca.archive_recipe(SEED_CATALOG, "b3")
    assert archived == replace(spec_of(SEED_CATALOG.recipes["b3"]), status="archived")
    again = ca.archive_recipe(with_specs(), "b3")  # повтор — не ошибка
    assert again.status == "archived"
    assert ca.restore_recipe(SEED_CATALOG, "b3").status == "active"


def test_slot_recipe_is_not_archived():
    assert errors(lambda: ca.archive_recipe(SEED_CATALOG, "d3")) == [
        (
            ("id",),
            (
                "«Тёплый салат с креветками» стоит в цикле, позиция 3 — сначала"
                " замените его"
            ),
        )
    ]


def test_rotation_breakfast_is_not_archived():
    assert errors(lambda: ca.archive_recipe(SEED_CATALOG, "b1")) == [
        (("id",), "«Яичница-глазунья» — завтрак в ротации, в архив нельзя")
    ]


def test_archive_unknown():
    assert errors(lambda: ca.restore_recipe(SEED_CATALOG, "x1")) == [
        (("id",), "Рецепта x1 нет")
    ]


# --- set-cycle-slot ---


def test_set_cycle_slot():
    slot = ca.set_cycle_slot(SEED_CATALOG, 2, "l1", "d5")
    assert (slot.slot, slot.lunch_id, slot.dinner_id) == (2, "l1", "d5")


def test_cycle_slot_errors():
    archived = replace(RECIPE_SPECS["d1"], id="d6", status="archived")
    assert errors(lambda: ca.set_cycle_slot(with_specs(archived), 6, "d1", "d6")) == [
        (("slot",), "Позиция цикла — от 1 до 5"),
        (("lunch_id",), "«Салат с курицей гриль» — не обед"),
        (("dinner_id",), "«Салат с курицей гриль» в архиве — сначала верните его"),
    ]
    assert errors(lambda: ca.set_cycle_slot(SEED_CATALOG, 1, "l9", "d1")) == [
        (("lunch_id",), "Рецепта l9 нет")
    ]


# --- save-ingredient ---


def ingredient(**changes) -> IngredientInput:
    base = IngredientInput(
        id=None,
        name="Тофу",
        unit="г",
        category="Белок",
        kcal_100g=76,
        protein_100g=8.08,
        source=" fdc 172475 Tofu, raw ",
        grams_per_unit=50,  # для г не хранится
    )
    return replace(base, **changes)


def test_new_ingredient():
    saved = ca.save_ingredient(SEED_CATALOG, ingredient())
    assert saved.id == len(SEED_CATALOG.ingredients) + 1
    assert (saved.name, saved.source, saved.grams_per_unit) == (
        "Тофу",
        "fdc 172475 Tofu, raw",
        None,
    )


def test_piece_unit_needs_grams():
    assert errors(
        lambda: ca.save_ingredient(
            SEED_CATALOG, ingredient(unit="шт", grams_per_unit=None)
        )
    ) == [(("grams_per_unit",), "Сколько граммов в «шт» — нужно число больше нуля")]
    saved = ca.save_ingredient(SEED_CATALOG, ingredient(unit="шт", grams_per_unit=120))
    assert saved.grams_per_unit == 120


def test_ingredient_errors():
    bad = ingredient(
        name="ЯЙЦА",
        unit="кг",
        category="Сладкое",
        kcal_100g=-1,
        protein_100g=101,
        store_piece=0,
    )
    assert errors(lambda: ca.save_ingredient(SEED_CATALOG, bad)) == [
        (("name",), "Ингредиент «ЯЙЦА» уже есть"),
        (("unit",), "Единица — одна из: г, мл, шт, ст.л., зубчик"),
        (
            ("category",),
            "Категория — одна из: Белок, Крупы, Овощи, Яйца и молочное, Прочее",
        ),
        (("kcal_100g",), "Ккал на 100 г не может быть меньше нуля"),
        (("protein_100g",), "Белок на 100 г — от 0 до 100"),
        (("store_piece",), "Штука в магазине — больше нуля или пусто"),
    ]


def test_rename_and_unit_of_used_ingredient():
    eggs = SEED_CATALOG.ingredients["яйца"]
    renamed = ca.save_ingredient(
        SEED_CATALOG,
        ingredient(
            id=eggs.id,
            name="яйца куриные",
            unit="шт",
            category=eggs.category,
            grams_per_unit=50,
        ),
    )
    assert (renamed.id, renamed.name) == (eggs.id, "яйца куриные")
    assert errors(
        lambda: ca.save_ingredient(
            SEED_CATALOG, ingredient(id=eggs.id, name="яйца", unit="г")
        )
    ) == [(("unit",), "Единицу нельзя менять: «яйца» есть в рецептах (b1, b3, d5)")]


def test_unknown_ingredient_id():
    assert errors(lambda: ca.save_ingredient(SEED_CATALOG, ingredient(id=999))) == [
        (("id",), "Такого ингредиента нет")
    ]


# --- plan_use ---


def recipe_ids(days) -> set[str]:
    return {r.id for d in days for r in (d.breakfast, d.lunch, d.dinner) if r}


def test_plan_use_bought_and_next_trip():
    # Пн 9 нояб.: едим купленное в Сб 7-го до Ср 11-го, закупка — Ср 11-го.
    today = date(2026, 11, 9)
    state = recompute(SEED_CATALOG, DEFAULT_2X, today)
    trip = state.next_shopping_trip
    assert trip.window_start == date(2026, 11, 12)
    eaten = recipe_ids(
        build_schedule(SEED_CATALOG, DEFAULT_2X, today, date(2026, 11, 11))
    )
    window = recipe_ids(
        build_schedule(SEED_CATALOG, DEFAULT_2X, trip.window_start, trip.window_end)
    )
    assert {r for r, use in state.plan_use.items() if use == "bought"} == eaten
    assert {r for r, use in state.plan_use.items() if use == "next_trip"} == (
        window - eaten
    )
    assert state.plan_use["b1"] == "bought"  # и там, и там — куплено важнее
    assert "b3" not in state.plan_use  # не в расписании


def test_plan_use_before_start_has_nothing_bought():
    today = DEFAULT_2X.meal_anchor_date - timedelta(days=5)
    state = recompute(SEED_CATALOG, DEFAULT_2X, today)
    assert set(state.plan_use.values()) == {"next_trip"}
    trip = next_shopping_trip(SEED_CATALOG, DEFAULT_2X, today)
    assert set(state.plan_use) == {
        r.id for c in trip.cook_items for r in (c.lunch, c.dinner)
    } | {"b1", "b2"}
