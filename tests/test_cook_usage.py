"""10.1: готовка любого рецепта по ингредиентам и журнал списаний; 10.6 —
два независимых потока: партия (plan) и вне очереди (extra)."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest

from meal_planner.core.actions import ActionError
from meal_planner.core.cookbook import cook_preview
from meal_planner.core.cooking import (
    CookState,
    cook_day,
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
L3 = (TODAY, "l3", "extra")  # вне очереди — сегодня
B5 = (BATCH_DAY, "l5", "plan")  # блюдо партии — день партии


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


# --- день готовки (10.5) ---


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 11, 10, 0, 30), date(2026, 11, 9)),  # ночь — ещё вчера
        (datetime(2026, 11, 10, 6, 59, 59), date(2026, 11, 9)),
        (datetime(2026, 11, 10, 7, 0), date(2026, 11, 10)),  # с 7:00 — новый день
        (datetime(2026, 11, 10, 23, 59), date(2026, 11, 10)),
    ],
)
def test_cook_day_starts_at_seven(now, expected):
    assert cook_day(now) == expected


# --- ключ ---


def key_of(recipe_id, scope):
    return cook_key(SEED_CATALOG, SETTINGS, TODAY, recipe_id, scope).key()


def test_cook_key_by_scope():
    batch = cook_key(SEED_CATALOG, SETTINGS, TODAY, "l5", "plan")
    assert (batch.key(), batch.planned) == (B5, 1)
    assert key_of("b1", "plan") == (TODAY, "b1", "plan")  # завтрак сегодня
    # Вне очереди — любой активный: сегодня, базовый рецепт, 1 порция, в том
    # числе блюдо партии и сегодняшний завтрак — свои ключи.
    other = cook_key(SEED_CATALOG, SETTINGS, TODAY, "l5", "extra")
    assert (other.key(), other.recipe, other.planned) == (
        (TODAY, "l5", "extra"),
        SEED_CATALOG.recipes["l5"],
        1,
    )
    assert key_of("b1", "extra") == (TODAY, "b1", "extra")
    assert key_of("l3", "extra") == L3
    # В партии l3 нет.
    assert error(cook_key, SEED_CATALOG, SETTINGS, TODAY, "l3", "plan") == (
        "recipe_id",
        "Это блюдо сейчас не готовится",
    )


def test_batch_dish_has_the_portion_for_the_target():
    scaled = replace(
        SETTINGS,
        daily_kcal_target=2700,
        portion_scales=(PortionScale(date(2026, 11, 5), 2700),),
    )
    target = cook_key(SEED_CATALOG, scaled, TODAY, "l5", "plan")
    assert target.recipe.scale == 1.5
    assert target.recipe.ingredients[0].qty == 255  # лосось 170 × 1.5
    # Вне очереди — базовый рецепт, даже у блюда партии.
    assert cook_key(SEED_CATALOG, scaled, TODAY, "l5", "extra").recipe.scale == 1


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
    assert error(cook_key, catalog, SETTINGS, TODAY, recipe_id, "extra") == (
        "recipe_id",
        message,
    )


# --- ингредиенты ---


def test_use_writes_off_in_counter_units():
    change = use(state(), "l3", AVOCADO, [line("авокадо", 1, "шт", "input_number.a")])
    # 35 г из штуки 130 г: 1 − 0.269… → 0.73, ушло 0.27 шт = 35.1 г.
    assert change.key == L3
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
    change = use(state(), "l5", ID["брокколи"], lines)  # вне очереди
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
    assert error(cook, state(), "l3", SHORT) == (
        "stock",
        (
            "Не хватает продуктов: паста цельнозерновая — нужно 90 г, есть 50 г;"
            " авокадо — нужно 35 г, есть 26 г"
        ),
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
    # Партия — только её блюда и завтрак.
    assert error(cook, state(), "l3", [], scope="plan") == (
        "recipe_id",
        "Это блюдо сейчас не готовится",
    )
    assert cook(state(), "l5", [], scope="plan").key == B5


# --- отмена ---


def uncook(st, recipe_id, lines, scope="extra"):
    return uncook_recipe(
        SEED_CATALOG, SETTINGS, TODAY, st, recipe_id, lines, scope=scope
    )


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
    st = state(cooked={B5: BATCH_DAY}, portions={B5: 2})
    back = uncook(st, "l5", [line("лосось", 0, "г", "input_number.salmon")], "plan")
    assert back.update.values == (("input_number.salmon", 340.0),)


def test_uncook_rules():
    assert error(uncook, state(), "l3", []) == (
        "recipe_id",
        "Блюдо не отмечено как приготовленное",
    )


# --- порции ---


def test_portions_until_the_first_write_off():
    def portions(st, recipe_id, n, scope="extra"):
        return set_recipe_portions(
            SEED_CATALOG, SETTINGS, TODAY, st, recipe_id, n, scope
        )

    assert portions(state(), "l3", 3) == L3
    assert portions(state(), "l5", 3, "plan") == B5
    # Списание вне очереди не блокирует порции партии.
    assert portions(state(usage={(TODAY, "l5", "extra"): {1: None}}), "l5", 3, "plan")
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
    p = preview(state(portions={L3: 2}), lines).extra["l3"]
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
    p = preview(state(usage={L3: {PASTA: Decimal(90)}}), lines).extra["l3"]
    assert (p.locked, p.missing) == (True, ())
    assert {i.ingredient_id: i.used for i in p.ingredients}[PASTA]


def test_preview_has_two_independent_streams():
    lines = [line("лосось", 0, "г", "input_number.salmon")]
    # Партию отметили (ключ — день партии): ряд «Сейчас» серый, а та же
    # плитка в группе — нет.
    p = preview(state(cooked={B5: BATCH_DAY}), lines)
    assert (p.plan["l5"].day, p.plan["l5"].cooked, p.plan["l5"].missing) == (
        BATCH_DAY,
        True,
        (),
    )
    assert (p.extra["l5"].day, p.extra["l5"].cooked) == (TODAY, False)
    # И наоборот: вне очереди партию не отмечает.
    p = preview(state(cooked={(TODAY, "l5", "extra"): TODAY}), lines)
    assert (p.plan["l5"].cooked, p.extra["l5"].cooked) == (False, True)
    # plan — только партия и сегодняшний завтрак.
    assert set(p.plan) == {"l5", "d5", "l2", "d2", "l4", "d4", "l1", "d1", "b1"}


def test_preview_covers_the_book_with_the_batch_portion():
    scaled = replace(
        SETTINGS,
        daily_kcal_target=2700,
        portion_scales=(PortionScale(date(2026, 11, 5), 2700),),
    )
    p = preview(state(), [], scaled)
    active = {r.id for r in SEED_CATALOG.recipes.values() if r.status == "active"}
    assert active == set(p.extra)

    def salmon(c):
        return {i.ingredient_id: i.need for i in c.ingredients}[ID["лосось"]]

    assert salmon(p.plan["l5"]) == Decimal(255)  # партия — порция под цель
    assert salmon(p.extra["l5"]) == Decimal(170)  # вне очереди — базовый рецепт


# --- потоки (10.6) ---


def test_streams_do_not_touch_each_other():
    lines = [line("лосось", 1000, "г", "input_number.salmon")]
    # Блюдо партии отмечено — вне очереди его можно приготовить отдельно.
    batch = state(cooked={B5: BATCH_DAY})
    extra = cook(batch, "l5", lines)
    assert extra.key == (TODAY, "l5", "extra")
    assert extra.update.values == (("input_number.salmon", 830.0),)  # 170, ×1
    # Отмена вне очереди партию не трогает, партия — вне очереди.
    both = state(cooked={B5: BATCH_DAY, extra.key: TODAY})
    assert uncook(both, "l5", lines).key == extra.key
    assert uncook(both, "l5", lines, "plan").key == B5
    # Завтрак: партия и вне очереди — два ключа одного дня.
    plan_b1 = cook(state(), "b1", [], scope="plan")
    assert cook(state(cooked={plan_b1.key: TODAY}), "b1", []).key == (
        TODAY,
        "b1",
        "extra",
    )
