"""10.1: готовка любого рецепта по ингредиентам и журнал списаний."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from meal_planner.core.actions import ActionError
from meal_planner.core.cookbook import cook_preview
from meal_planner.core.cooking import (
    CookState,
    check_enough,
    cook_key,
    cook_recipe,
    set_recipe_portions,
    uncook_recipe,
    unuse_ingredient,
    use_ingredient,
)
from meal_planner.core.models import PortionScale, Settings
from meal_planner.core.seed import SEED_CATALOG
from meal_planner.core.stock import StockLine

# Готовка Ср/Сб; якорь Чт 05.11. Пн 09.11: партия Сб 07.11 (l5, d5, l2, d2,
# l4, d4, l1, d1 — по порции), завтрак b1. l3 в партию не входит.
SETTINGS = Settings(date(2026, 11, 5), 2, 3, 6, "same")
TODAY = date(2026, 11, 9)
BATCH_DAY = date(2026, 11, 7)
ID = {i.name: i.id for i in SEED_CATALOG.ingredients.values()}
PASTA, AVOCADO, SPINACH = (
    ID["паста цельнозерновая"],
    ID["авокадо"],
    ID["шпинат"],
)
L3 = (TODAY, "l3")  # вне очереди — сегодня


def line(name, qty, unit, entity):
    return StockLine(ID[name], qty, unit, entity)


def state(cooked=None, usage=None, portions=None):
    return CookState(cooked or {}, usage or {}, portions or {})


def error(fn, *args, **kwargs):
    with pytest.raises(ActionError) as e:
        fn(*args, **kwargs)
    return e.value.field, e.value.message


def use(st, recipe_id, ingredient_id, lines):
    return use_ingredient(
        SEED_CATALOG, SETTINGS, TODAY, st, recipe_id, ingredient_id, lines
    )


# --- ключ ---


def test_cook_key_batch_breakfast_and_out_of_turn():
    batch = cook_key(SEED_CATALOG, SETTINGS, TODAY, "l5")
    assert (batch.day, batch.recipe.id, batch.planned) == (BATCH_DAY, "l5", 1)
    breakfast = cook_key(SEED_CATALOG, SETTINGS, TODAY, "b1")
    assert (breakfast.day, breakfast.planned) == (TODAY, 1)
    # Не из партии — вне очереди: сегодня, базовый рецепт, 1 порция.
    other = cook_key(SEED_CATALOG, SETTINGS, TODAY, "l3")
    assert (other.day, other.recipe, other.planned) == (
        TODAY,
        SEED_CATALOG.recipes["l3"],
        1,
    )
    # Чужой завтрак — тоже вне очереди.
    assert cook_key(SEED_CATALOG, SETTINGS, TODAY, "b2").day == TODAY


def test_batch_dish_has_the_portion_for_the_target():
    scaled = replace(
        SETTINGS,
        daily_kcal_target=2700,
        portion_scales=(PortionScale(date(2026, 11, 5), 2700),),
    )
    target = cook_key(SEED_CATALOG, scaled, TODAY, "l5")
    assert target.recipe.scale == 1.5
    assert target.recipe.ingredients[0].qty == 255  # лосось 170 × 1.5
    # Вне очереди — базовый рецепт.
    assert cook_key(SEED_CATALOG, scaled, TODAY, "l3").recipe.scale == 1


@pytest.mark.parametrize(
    ("status", "recipe_id", "message"),
    [
        ("draft", "l3", "Рецепт не активен — его не готовят"),
        ("archived", "l3", "Рецепт не активен — его не готовят"),
        ("active", "нет", "Нет такого рецепта"),
    ],
)
def test_inactive_or_unknown_recipe_is_not_cooked(status, recipe_id, message):
    recipes = dict(SEED_CATALOG.recipes)
    recipes["l3"] = replace(recipes["l3"], status=status)
    catalog = replace(SEED_CATALOG, recipes=recipes)
    assert error(cook_key, catalog, SETTINGS, TODAY, recipe_id) == (
        "recipe_id",
        message,
    )


# --- ингредиенты ---


def test_use_writes_off_in_counter_units():
    change = use(state(), "l3", AVOCADO, [line("авокадо", 1, "шт", "input_number.a")])
    # 35 г из штуки 130 г: 1 − 0.269… → 0.73, ушло 0.27 шт = 35.1 г.
    assert (change.day, change.recipe_id) == L3
    assert change.update.values == (("input_number.a", 0.73),)
    assert change.amounts == {AVOCADO: Decimal("35.1")}


def test_use_with_shortage_writes_off_what_is_there():
    lines = [line("паста цельнозерновая", 50, "г", "input_number.pasta")]
    change = use(state(), "l3", PASTA, lines)  # нужно 90 г
    assert change.update.values == (("input_number.pasta", 0.0),)
    assert change.amounts == {PASTA: Decimal(50)}


def test_use_without_counter_only_marks():
    change = use(state(), "l3", SPINACH, [])
    assert change.amounts == {SPINACH: None}
    assert change.update.untracked == ("шпинат",)
    # Счётчик есть, но шт брокколи не перевести — тоже только отметка.
    lines = [line("брокколи", 2, "шт", "input_number.broccoli")]
    change = use(state(), "l5", ID["брокколи"], lines)
    assert change.amounts == {ID["брокколи"]: None}
    assert len(change.update.ignored) == 1


def test_use_takes_the_chosen_portions():
    lines = [line("паста цельнозерновая", 500, "г", "input_number.pasta")]
    change = use(state(portions={L3: 3}), "l3", PASTA, lines)
    assert change.update.values == (("input_number.pasta", 230.0),)  # 500 − 270


def test_use_rules():
    used = state(usage={L3: {PASTA: Decimal(90)}})
    cooked = state(cooked={L3: TODAY})
    assert error(use, used, "l3", PASTA, []) == (
        "ingredient_id",
        "Ингредиент уже использован",
    )
    assert error(use, cooked, "l3", AVOCADO, []) == (
        "recipe_id",
        "Блюдо уже отмечено как приготовленное",
    )
    assert error(use, state(), "l3", ID["лосось"], []) == (
        "ingredient_id",
        "Этого ингредиента нет в рецепте",
    )


def test_unuse_returns_exactly_what_was_written_off():
    before = [line("авокадо", 1, "шт", "input_number.a")]
    used = use(state(), "l3", AVOCADO, before)
    after = [line("авокадо", used.update.values[0][1], "шт", "input_number.a")]
    back = unuse_ingredient(
        SEED_CATALOG,
        SETTINGS,
        TODAY,
        state(usage={L3: used.amounts}),
        "l3",
        AVOCADO,
        after,
    )
    assert back.update.values == (("input_number.a", 1.0),)
    assert back.amounts == used.amounts
    # Отмеченный без счётчика — счётчики не трогаются.
    back = unuse_ingredient(
        SEED_CATALOG,
        SETTINGS,
        TODAY,
        state(usage={L3: {SPINACH: None}}),
        "l3",
        SPINACH,
        [line("шпинат", 0, "г", "input_number.spinach")],
    )
    assert (back.update.values, back.amounts) == ((), {SPINACH: None})


def test_unuse_rules():
    def unuse(st, ingredient_id):
        return unuse_ingredient(
            SEED_CATALOG, SETTINGS, TODAY, st, "l3", ingredient_id, []
        )

    assert error(unuse, state(), PASTA) == (
        "ingredient_id",
        "Ингредиент не использован",
    )
    cooked = state(cooked={L3: TODAY}, usage={L3: {PASTA: Decimal(90)}})
    assert error(unuse, cooked, PASTA) == (
        "recipe_id",
        "Блюдо приготовлено — сначала снимите отметку",
    )


# --- готовка целиком ---


def cook(st, recipe_id, lines, **kwargs):
    return cook_recipe(SEED_CATALOG, SETTINGS, TODAY, st, recipe_id, lines, **kwargs)


def test_cook_writes_off_the_rest():
    lines = [
        line("паста цельнозерновая", 500, "г", "input_number.pasta"),
        line("авокадо", 1, "шт", "input_number.a"),
    ]
    change = cook(state(), "l3", lines)
    assert change.update.values == (
        ("input_number.pasta", 410.0),
        ("input_number.a", 0.73),
    )
    # Журнал — по каждому ингредиенту, без счётчика — None.
    assert change.amounts == {
        i.id: (
            Decimal(90)
            if i.id == PASTA
            else Decimal("35.1")
            if i.id == AVOCADO
            else None
        )
        for i in (
            SEED_CATALOG.ingredients[ing.name]
            for ing in SEED_CATALOG.recipes["l3"].ingredients
        )
    }


SHORT = [
    line("паста цельнозерновая", 50, "г", "input_number.pasta"),  # из 90
    line("авокадо", 0.2, "шт", "input_number.a"),  # 26 г из 35
]


def test_cook_with_shortage_needs_confirmation():
    expected = error(check_enough, SEED_CATALOG, SEED_CATALOG.recipes["l3"], 1, SHORT)
    assert error(cook, state(), "l3", SHORT) == expected
    assert expected[1] == (
        "Не хватает продуктов: паста цельнозерновая — нужно 90 г, есть 50 г;"
        " авокадо — нужно 35 г, есть 26 г"
    )
    change = cook(state(), "l3", SHORT, allow_missing=True)
    assert change.update.values == (
        ("input_number.pasta", 0.0),
        ("input_number.a", 0.0),
    )
    assert change.amounts[PASTA] == Decimal(50)
    assert change.amounts[AVOCADO] == Decimal(26)


def test_cook_skips_used_ingredients():
    used = state(usage={L3: {PASTA: Decimal(50)}})
    lines = [SHORT[0], line("авокадо", 1, "шт", "input_number.a")]
    change = cook(used, "l3", lines)  # паста уже списана — нехватки нет
    assert PASTA not in change.amounts
    assert change.update.values == (("input_number.a", 0.73),)


def test_cook_rules():
    assert error(cook, state(cooked={L3: TODAY}), "l3", []) == (
        "recipe_id",
        "Блюдо уже отмечено как приготовленное",
    )
    # «Готовка» (8.3b) — только партия и завтрак.
    assert error(cook, state(), "l3", [], batch_only=True) == (
        "recipe_id",
        "Это блюдо сейчас не готовится",
    )
    assert cook(state(), "l5", [], batch_only=True).day == BATCH_DAY


# --- отмена ---


def uncook(st, recipe_id, lines):
    return uncook_recipe(SEED_CATALOG, SETTINGS, TODAY, st, recipe_id, lines)


def test_uncook_returns_the_journal():
    usage = {L3: {PASTA: Decimal(50), AVOCADO: Decimal(26), SPINACH: None}}
    lines = [
        line("паста цельнозерновая", 0, "г", "input_number.pasta"),
        line("авокадо", 0, "шт", "input_number.a"),
    ]
    back = uncook(state(cooked={L3: TODAY}, usage=usage), "l3", lines)
    assert back.update.values == (("input_number.pasta", 50.0), ("input_number.a", 0.2))
    assert back.update.untracked == ("шпинат",)
    assert back.amounts == usage[L3]


def test_uncook_old_mark_returns_the_recipe():
    # Отметка 8.3b без журнала — по рецепту на порции ключа.
    st = state(cooked={(BATCH_DAY, "l5"): BATCH_DAY}, portions={(BATCH_DAY, "l5"): 2})
    back = uncook(st, "l5", [line("лосось", 0, "г", "input_number.salmon")])
    assert back.update.values == (("input_number.salmon", 340.0),)


def test_uncook_rules():
    assert error(uncook, state(), "l3", []) == (
        "recipe_id",
        "Блюдо не отмечено как приготовленное",
    )


# --- порции ---


def test_portions_until_the_first_write_off():
    def portions(st, recipe_id, n):
        return set_recipe_portions(SEED_CATALOG, SETTINGS, TODAY, st, recipe_id, n)

    assert portions(state(), "l3", 3) == TODAY
    assert portions(state(), "l5", 3) == BATCH_DAY
    cases = [
        (
            state(usage={L3: {SPINACH: None}}),
            3,
            ("recipe_id", "Продукты уже списаны — порции меняются до первого списания"),
        ),
        (
            state(cooked={L3: TODAY}),
            3,
            ("recipe_id", "Блюдо уже отмечено — порции меняются до отметки"),
        ),
        (state(), 0, ("portions", "Порций — от 1 до 20")),
        (state(), 21, ("portions", "Порций — от 1 до 20")),
    ]
    for st, n, expected in cases:
        assert error(portions, st, "l3", n) == expected


# --- предпросмотр ---


def preview(st, lines, settings=SETTINGS):
    return cook_preview(SEED_CATALOG, settings, TODAY, st, lines)


def test_preview_needs_and_shortages():
    lines = [
        line("паста цельнозерновая", 100, "г", "input_number.pasta"),
        line("авокадо", 1, "шт", "input_number.a"),
    ]
    p = preview(state(portions={L3: 2}), lines)["l3"]
    assert (p.day, p.portions, p.planned_portions, p.locked) == (TODAY, 2, 1, False)
    by_id = {i.ingredient_id: i for i in p.ingredients}
    assert (by_id[PASTA].need, by_id[PASTA].have, by_id[PASTA].short) == (
        Decimal(180),
        Decimal(100),
        True,
    )
    assert (by_id[AVOCADO].need, by_id[AVOCADO].short) == (Decimal(70), False)
    assert (by_id[SPINACH].have, by_id[SPINACH].short) == (None, False)
    assert p.missing == ("паста цельнозерновая — нужно 180 г, есть 100 г",)

    # Использованное не в нехватке; порции заблокированы.
    p = preview(state(usage={L3: {PASTA: Decimal(90)}}), lines)["l3"]
    assert (p.locked, p.missing) == (True, ())
    assert {i.ingredient_id: i.used for i in p.ingredients}[PASTA]


def test_preview_cooked_today_is_by_the_day_of_cooking():
    key = (BATCH_DAY, "l5")
    lines = [line("лосось", 0, "г", "input_number.salmon")]
    yesterday = preview(state(cooked={key: date(2026, 11, 8)}), lines)["l5"]
    assert (yesterday.day, yesterday.cooked, yesterday.cooked_today) == (
        BATCH_DAY,
        True,
        False,
    )
    assert yesterday.missing == ()  # приготовлено — нехватки нет
    assert preview(state(cooked={key: TODAY}), lines)["l5"].cooked_today


def test_preview_covers_the_book_with_the_batch_portion():
    scaled = replace(
        SETTINGS,
        daily_kcal_target=2700,
        portion_scales=(PortionScale(date(2026, 11, 5), 2700),),
    )
    p = preview(state(), [], scaled)
    active = {r.id for r in SEED_CATALOG.recipes.values() if r.status == "active"}
    assert active <= set(p)
    salmon = {i.ingredient_id: i.need for i in p["l5"].ingredients}[ID["лосось"]]
    assert salmon == Decimal(255)
