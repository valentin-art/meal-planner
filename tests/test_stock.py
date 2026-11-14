"""8.2: остатки из HA — перевод единиц и покупки за вычетом остатков."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from meal_planner.core.models import CatalogIngredient, Ingredient, Recipe, Settings
from meal_planner.core.planner import recompute, shopping_with_stock
from meal_planner.core.seed import SEED_CATALOG
from meal_planner.core.stock import (
    StockIgnored,
    StockLine,
    convertible_units,
    stock_in_catalog_units,
    unit_fixes,
    unstocked_ingredients,
)

# Сегодня Пн 09.11: закупка Ср 11.11, окно Чт–Сб (как default_2x).
SETTINGS = Settings(date(2026, 11, 5), 2, 3, 6, "same")
TODAY = date(2026, 11, 9)
ID = {i.name: i.id for i in SEED_CATALOG.ingredients.values()}


def line(name: str, qty: float, unit: str) -> StockLine:
    return StockLine(ID[name], qty, unit)


def converted(*lines: StockLine) -> dict[str, Decimal]:
    stock, ignored = stock_in_catalog_units(SEED_CATALOG, lines)
    assert ignored == []
    return stock


def items(*lines: StockLine) -> dict[str, tuple[float, str, int | None]]:
    trip = shopping_with_stock(SEED_CATALOG, SETTINGS, TODAY, lines).trip
    return {
        i.name: (i.qty, i.unit, i.pieces) for g in trip.shopping_groups for i in g.items
    }


def test_units_convert_into_ingredient_units():
    assert converted(
        line("куриное филе", 0.2, "кг"),  # кг → г
        line("сок лимона", 0.01, "л"),  # л → мл
        line("яйца", 4, "шт"),  # та же единица
        line("авокадо", 1, "шт"),  # шт → г по штуке в магазине (130 г)
        line("масло сливочное", 7.1, "г"),  # г → ст.л. (14.2 г)
        line("яйца", 60, "г"),  # г → шт (50 г), складывается с 4 шт
        line("оливковое масло", 45, "мл"),  # мл → ст.л. (15 мл)
    ) == {
        "куриное филе": Decimal(200),
        "сок лимона": Decimal(10),
        "яйца": Decimal("5.2"),
        "авокадо": Decimal(130),
        "масло сливочное": Decimal("0.5"),
        "оливковое масло": Decimal(3),
    }


def test_lines_of_one_ingredient_add_up_and_zero_is_skipped():
    assert converted(
        line("куриное филе", 100, "г"),
        line("куриное филе", 0.1, "кг"),
        line("лосось", 0, "г"),
    ) == {"куриное филе": Decimal(200)}


def test_unusable_lines_are_ignored_with_reason():
    lines = [
        StockLine(999, 1, "шт"),
        line("брокколи", 1, "шт"),
        line("чеснок", 1, "шт"),
        line("сок лимона", 40, "г"),  # мл ↔ г без плотности не переводится
        line("лосось", -50, "г"),
    ]
    stock, ignored = stock_in_catalog_units(SEED_CATALOG, lines)
    assert stock == {}
    assert ignored == [
        StockIgnored(999, None, 1, "шт", "нет такого ингредиента в справочнике"),
        StockIgnored(
            ID["брокколи"],
            "брокколи",
            1,
            "шт",
            "не перевести шт в г: у ингредиента нет веса штуки в магазине",
        ),
        StockIgnored(
            ID["чеснок"],
            "чеснок",
            1,
            "шт",
            "не перевести шт в зубчик: у ингредиента нет веса штуки в магазине",
        ),
        StockIgnored(ID["сок лимона"], "сок лимона", 40, "г", "не перевести г в мл"),
        StockIgnored(ID["лосось"], "лосось", -50, "г", "отрицательный остаток: -50 г"),
    ]


def test_without_stock_trip_is_the_plan():
    stocked = shopping_with_stock(SEED_CATALOG, SETTINGS, TODAY, [])
    assert stocked.trip == recompute(SEED_CATALOG, SETTINGS, TODAY).next_shopping_trip
    assert stocked.ignored == ()


def test_stock_is_subtracted_and_pieces_follow_the_rest():
    before = items()
    assert before["авокадо"] == (200, "г", 2)
    assert before["куриное филе"] == (480, "г", None)
    after = items(
        line("авокадо", 1, "шт"),
        line("куриное филе", 0.2, "кг"),
        line("оливковое масло", 45, "мл"),
    )
    assert after["авокадо"] == (70, "г", 1)
    assert after["куриное филе"] == (280, "г", None)
    assert after["оливковое масло"] == (4.5, "ст.л.", None)
    # Остальное — без изменений.
    unchanged = set(before) - {"авокадо", "куриное филе", "оливковое масло"}
    assert {k: after[k] for k in unchanged} == {k: before[k] for k in unchanged}


def test_enough_at_home_drops_the_item():
    after = items(line("яйца", 6, "шт"), line("сок лимона", 1, "шт"))
    assert "яйца" not in after
    assert "сок лимона" not in after  # 40 мл, лимон — 45 мл
    assert items(line("яйца", 4, "шт"))["яйца"] == (2, "шт", None)


def test_rounding_up_happens_after_subtraction():
    # 480 − 100.04 = 379.96 → вверх до 0.1 → 380, а не 380 − ceil(100.04).
    assert items(line("куриное филе", 100.04, "г"))["куриное филе"][0] == 380


def test_same_trip_as_recompute_with_skipped_cook():
    # Готовка Ср 11.11 пропущена (7.7): закупка — к Сб 14.11.
    skipped = Settings(date(2026, 11, 5), 2, 3, 6, "same", date(2026, 11, 11))
    stocked = shopping_with_stock(SEED_CATALOG, skipped, TODAY, [])
    assert stocked.trip == recompute(SEED_CATALOG, skipped, TODAY).next_shopping_trip
    assert stocked.trip.shopping_date == date(2026, 11, 14)


# --- ингредиенты без позиции (4.4) ---


def ingredient(unit, store_piece=None, grams_per_unit=None, **kw):
    return CatalogIngredient(
        kw.get("id", 1),
        kw.get("name", "x"),
        unit,
        kw.get("category", "Прочее"),
        0,
        0,
        None,
        grams_per_unit,
        store_piece,
    )


def catalog_with(recipes, ingredients):
    return replace(
        SEED_CATALOG,
        recipes={r.id: r for r in recipes},
        ingredients={i.name: i for i in ingredients},
    )


def recipe(rid, status, *names, kind="lunch"):
    return Recipe(
        rid,
        rid,
        kind,
        0,
        0,
        tuple(Ingredient(n, 1, "г", "Прочее") for n in names),
        (),
        status=status,
    )


def test_unstocked_picks_active_recipes_ingredients_without_a_line():
    a = ingredient("г", id=1, name="а", category="Овощи")
    b = ingredient("г", id=2, name="б", category="Белок")
    c = ingredient("г", id=3, name="в", category="Белок")
    d = ingredient("г", id=4, name="г", category="Странная")
    e = ingredient("г", id=5, name="д", category="Белок")  # только в черновике
    f = ingredient("г", id=6, name="е")  # только в архиве
    g = ingredient("г", id=7, name="ж")  # с позицией qty 0
    h = ingredient("г", id=8, name="з")  # с позицией в чужой единице
    k = ingredient("г", id=9, name="и", category="Белок")  # только в завтраке
    catalog = catalog_with(
        [
            recipe("l1", "active", "а", "б", "в", "г", "ж", "з"),
            recipe("d1", "active", "б"),  # дубль между рецептами
            recipe("l2", "draft", "д"),
            recipe("l3", "archived", "е"),
            recipe("b1", "active", "и", kind="breakfast"),
        ],
        [a, b, c, d, e, f, g, h, k],
    )
    lines = [
        StockLine(7, 0, "г"),
        StockLine(8, -1, "стакан"),
        StockLine(999, 1, "г"),  # неизвестный id
    ]
    got = unstocked_ingredients(catalog, lines)
    # Белок (по имени), потом Овощи, чужая категория в конце.
    assert [i.name for i in got] == ["б", "в", "и", "а", "г"]
    assert unstocked_ingredients(catalog, [StockLine(2, 1, "г")]) != got
    assert [i.id for i in unstocked_ingredients(catalog, [])] == [2, 3, 9, 1, 7, 8, 4]


@pytest.mark.parametrize(
    ("unit", "piece", "grams", "expected"),
    [
        ("г", None, None, ("г", "кг")),
        ("г", 50, None, ("г", "кг", "шт")),
        ("мл", None, None, ("мл", "л")),
        ("мл", 200, None, ("мл", "л", "шт")),
        ("шт", None, None, ("шт",)),
        ("шт", None, 50, ("г", "кг", "шт")),
        ("ч.л.", None, None, ("ч.л.",)),
        ("ч.л.", 10, 5, ("г", "кг", "ч.л.", "шт")),
        ("ст.л.", None, None, ("мл", "л", "ст.л.")),
        ("ст.л.", 10, 15, ("г", "кг", "мл", "л", "ст.л.", "шт")),
        ("зубчик", None, None, ("зубчик",)),
        ("зубчик", 8, 4, ("г", "кг", "шт", "зубчик")),
    ],
)
def test_convertible_units(unit, piece, grams, expected):
    assert convertible_units(ingredient(unit, piece, grams)) == expected


def test_unit_fixes_suggest_only_empty_fields():
    assert unit_fixes(ingredient("г")) == (("store_piece", ("шт",)),)
    assert unit_fixes(ingredient("шт")) == (("grams_per_unit", ("г", "кг")),)
    assert unit_fixes(ingredient("шт", grams_per_unit=50)) == ()
    assert unit_fixes(ingredient("г", store_piece=50)) == ()
