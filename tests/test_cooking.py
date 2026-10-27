"""8.3b: партия готовки, отметки «приготовлено» и списание из счётчиков."""

from dataclasses import replace
from datetime import date

import pytest

from meal_planner.core.actions import ActionError
from meal_planner.core.cooking import (
    CounterUpdate,
    check_enough,
    cooked_change,
    cooking,
    counter_update,
    current_cook_date,
)
from meal_planner.core.models import (
    CycleSlot,
    Ingredient,
    MealKind,
    Recipe,
    Settings,
)
from meal_planner.core.planner import recompute
from meal_planner.core.scheduling import build_schedule
from meal_planner.core.seed import SEED_CATALOG
from meal_planner.core.stock import StockIgnored, StockLine

# Готовка Ср/Сб; якорь Чт 05.11 — день 0 Ср 04.11 (совпал с днём готовки).
SETTINGS = Settings(date(2026, 11, 5), 2, 3, 6, "same")
ID = {i.name: i.id for i in SEED_CATALOG.ingredients.values()}


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 11, 3), None),  # до дня 0
        (date(2026, 11, 4), date(2026, 11, 4)),  # день 0
        (date(2026, 11, 6), date(2026, 11, 4)),
        (date(2026, 11, 7), date(2026, 11, 7)),  # день готовки
        (date(2026, 11, 10), date(2026, 11, 7)),
    ],
)
def test_current_cook_date(today, expected):
    assert current_cook_date(SETTINGS, today) == expected


def test_day_zero_not_on_cook_weekday_is_the_first_batch():
    # Якорь Пн 09.11: день 0 — Вс 08.11, не день готовки (6.5).
    s = replace(SETTINGS, meal_anchor_date=date(2026, 11, 9))
    assert current_cook_date(s, date(2026, 11, 8)) == date(2026, 11, 8)
    assert current_cook_date(s, date(2026, 11, 10)) == date(2026, 11, 8)
    assert current_cook_date(s, date(2026, 11, 11)) == date(2026, 11, 11)


def test_skipped_cook_has_no_batch():
    # Готовка Ср 11.11 пропущена (7.7): пауза Чт–Сб, в Сб снова готовка.
    s = replace(SETTINGS, skipped_cook_date=date(2026, 11, 11))
    assert current_cook_date(s, date(2026, 11, 10)) == date(2026, 11, 7)
    assert current_cook_date(s, date(2026, 11, 12)) is None
    assert cooking(SEED_CATALOG, s, date(2026, 11, 12), set()).batch is None
    assert cooking(SEED_CATALOG, s, date(2026, 11, 12), set()).breakfast is None
    assert current_cook_date(s, date(2026, 11, 14)) == date(2026, 11, 14)


@pytest.mark.parametrize("timing", ["same", "before"])
def test_batch_is_what_the_trip_cooks(timing):
    # Поездка за день до готовки Ср 11.11 — в Вт 10.11; партия — в Ср.
    s = replace(SETTINGS, shopping_timing=timing)
    trip = recompute(SEED_CATALOG, s, date(2026, 11, 9)).next_shopping_trip
    assert trip.cook_date == date(2026, 11, 11)
    expected: dict[str, int] = {}
    for c in trip.cook_items:
        for recipe in (c.lunch, c.dinner):
            expected[recipe.id] = expected.get(recipe.id, 0) + c.portions
    batch = cooking(SEED_CATALOG, s, date(2026, 11, 11), set()).batch
    assert batch.cook_date == date(2026, 11, 11)
    assert {d.recipe.id: d.portions for d in batch.dishes} == expected


def test_recipe_in_two_slots_is_one_dish():
    # Обед позиции 1 поставлен и в позицию 2 (с 5.3 так можно).
    slots = list(SEED_CATALOG.slots)
    slots[1] = CycleSlot(2, slots[0].lunch_id, slots[1].dinner_id)
    catalog = replace(SEED_CATALOG, slots=tuple(slots))
    # Партия Сб 07.11 ест Вс–Ср: позиции 5, 2, 4, 1.
    batch = cooking(catalog, SETTINGS, date(2026, 11, 9), set()).batch
    expected: dict[str, int] = {}
    for d in build_schedule(catalog, SETTINGS, date(2026, 11, 8), date(2026, 11, 11)):
        for recipe in (d.lunch, d.dinner):
            expected[recipe.id] = expected.get(recipe.id, 0) + 1
    assert expected[slots[0].lunch_id] == 2
    ids = [d.recipe.id for d in batch.dishes]
    assert len(ids) == len(set(ids))
    assert {d.recipe.id: d.portions for d in batch.dishes} == expected


def test_breakfast_today_and_cooked_flags():
    today = date(2026, 11, 9)
    state = cooking(SEED_CATALOG, SETTINGS, today, set())
    breakfast = state.breakfast.recipe.id
    lunch = state.batch.dishes[0].recipe.id
    cooked = {(today, breakfast), (date(2026, 11, 7), lunch)}
    state = cooking(SEED_CATALOG, SETTINGS, today, cooked)
    assert state.breakfast.cooked
    assert state.batch.dishes[0].cooked
    assert not any(d.cooked for d in state.batch.dishes[1:])
    # В день 0 завтрака нет — блюда с завтра.
    assert cooking(SEED_CATALOG, SETTINGS, date(2026, 11, 4), set()).breakfast is None


def test_cooked_change_rules():
    today = date(2026, 11, 9)
    state = cooking(SEED_CATALOG, SETTINGS, today, set())
    breakfast = state.breakfast.recipe.id
    dish = state.batch.dishes[0]

    change = cooked_change(SEED_CATALOG, SETTINGS, today, set(), breakfast, True)
    assert (change.day, change.portions) == (today, 1)
    change = cooked_change(SEED_CATALOG, SETTINGS, today, set(), dish.recipe.id, True)
    assert (change.day, change.recipe, change.portions) == (
        date(2026, 11, 7),
        dish.recipe,
        dish.portions,
    )

    cooked = {(date(2026, 11, 7), dish.recipe.id)}
    cases = [
        (set(), "l3", True, "Это блюдо сейчас не готовится"),  # не в партии
        (set(), "нет", True, "Это блюдо сейчас не готовится"),
        (cooked, dish.recipe.id, True, "Блюдо уже отмечено как приготовленное"),
        (set(), dish.recipe.id, False, "Блюдо не отмечено как приготовленное"),
    ]
    assert "l3" not in {d.recipe.id for d in state.batch.dishes}
    for marks, recipe_id, mark, message in cases:
        with pytest.raises(ActionError) as e:
            cooked_change(SEED_CATALOG, SETTINGS, today, marks, recipe_id, mark)
        assert (e.value.field, e.value.message) == ("recipe_id", message)
    unmark = cooked_change(SEED_CATALOG, SETTINGS, today, cooked, dish.recipe.id, False)
    assert unmark.day == date(2026, 11, 7)


RECIPE = Recipe(
    id="x",
    name="тест",
    kind=MealKind.LUNCH,
    kcal=0,
    protein=0,
    ingredients=(
        Ingredient("авокадо", 130, "г", "Овощи"),  # штука — 130 г
        Ingredient("оливковое масло", 1.5, "ст.л.", "Прочее"),  # 22.5 мл
        Ingredient("масло сливочное", 1, "ст.л.", "Прочее"),  # 14.2 г
        Ingredient("куриное филе", 300, "г", "Белок"),
        Ingredient("брокколи", 100, "г", "Овощи"),  # штука не задана
        Ingredient("киноа", 60, "г", "Крупы"),  # без счётчика
    ),
    steps=(),
)


def line(name, qty, unit, entity):
    return StockLine(ID[name], qty, unit, entity)


def test_write_off_in_counter_units_not_below_zero():
    update = counter_update(
        SEED_CATALOG,
        RECIPE,
        2,
        [
            line("авокадо", 3, "шт", "input_number.avocado"),
            line("оливковое масло", 100, "мл", "input_number.olive_oil"),
            line("масло сливочное", 20, "г", "input_number.butter"),
            line("куриное филе", 1, "кг", "input_number.chicken"),
            line("брокколи", 2, "шт", "input_number.broccoli"),
            StockLine(ID["киноа"], 500, "г"),  # без сущности — не выставить
        ],
        -1,
    )
    assert update == CounterUpdate(
        values=(
            ("input_number.avocado", 1.0),
            ("input_number.olive_oil", 55.0),
            ("input_number.butter", 0.0),  # 28.4 г из 20 — не ниже 0
            ("input_number.chicken", 0.4),
        ),
        ignored=(
            StockIgnored(
                ID["брокколи"],
                "брокколи",
                2,
                "шт",
                "не перевести шт в г: у ингредиента нет веса штуки в магазине",
            ),
        ),
        untracked=("киноа",),
    )


def test_unmark_puts_the_dish_back():
    update = counter_update(
        SEED_CATALOG,
        RECIPE,
        1,
        [
            line("авокадо", 0.25, "шт", "input_number.avocado"),
            line("масло сливочное", 0, "г", "input_number.butter"),
        ],
        1,
    )
    assert update.values == (
        ("input_number.avocado", 1.25),
        ("input_number.butter", 14.2),
    )


def test_several_counters_of_one_ingredient_in_order():
    lines = [
        line("куриное филе", 0.2, "кг", "input_number.chicken_a"),
        line("куриное филе", 1000, "г", "input_number.chicken_b"),
    ]
    off = counter_update(SEED_CATALOG, RECIPE, 2, lines, -1)  # нужно 600 г
    chicken = [v for v in off.values if v[0].startswith("input_number.chicken")]
    assert chicken == [
        ("input_number.chicken_a", 0.0),
        ("input_number.chicken_b", 600.0),
    ]
    back = counter_update(SEED_CATALOG, RECIPE, 2, lines, 1)  # всё — в первый
    assert [v for v in back.values if v[0].startswith("input_number.chicken")] == [
        ("input_number.chicken_a", 0.8),
    ]


def test_counter_values_are_rounded():
    # Цукини — 280 г штука: 100 г — 0.357… шт, до сотых.
    recipe = replace(RECIPE, ingredients=(Ingredient("цукини", 100, "г", "Овощи"),))
    update = counter_update(
        SEED_CATALOG, recipe, 1, [line("цукини", 1, "шт", "input_number.zucchini")], -1
    )
    assert update.values == (("input_number.zucchini", 0.64),)  # 1 − 100/280


def test_enough_stock_lets_the_dish_be_marked():
    lines = [
        line("авокадо", 2, "шт", "input_number.avocado"),  # 260 г — ровно
        line("оливковое масло", 45, "мл", "input_number.olive_oil"),  # 3 ст.л.
        line("куриное филе", 0.4, "кг", None),  # 400 г, сущность не нужна
        line("куриное филе", 200, "г", None),  # +200 г — вместе 600
        line("брокколи", 0, "шт", "input_number.broccoli"),  # шт не перевести
    ]
    # Масло сливочное и киноа без счётчика — не проверяются.
    check_enough(SEED_CATALOG, RECIPE, 2, lines)


def test_not_enough_stock_refuses_the_mark():
    lines = [
        line("авокадо", 1.5, "шт", "input_number.avocado"),  # 195 из 260 г
        line("оливковое масло", 30, "мл", "input_number.olive_oil"),  # 2 из 3
        line("куриное филе", 1, "кг", "input_number.chicken"),
    ]
    with pytest.raises(ActionError) as e:
        check_enough(SEED_CATALOG, RECIPE, 2, lines)
    assert e.value.field == "stock"
    assert e.value.message == (
        "Не хватает продуктов: авокадо — нужно 260 г, есть 195 г;"
        " оливковое масло — нужно 3 ст.л., есть 2 ст.л."
    )
