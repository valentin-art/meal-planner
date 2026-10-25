from datetime import date
from decimal import Decimal

import pytest
from golden_shopping import FMT_QTY_CASES, NAME_ORDER, SHOPPING_CASES
from scenario_settings import settings_of

from meal_planner.core.recipes_data import ALL_RECIPES
from meal_planner.core.scheduling import build_schedule
from meal_planner.core.shopping import (
    aggregate,
    count_portions,
    extract_shopping_list,
    format_qty,
)

R = ALL_RECIPES


def window_days(case):
    return build_schedule(
        settings_of(case),
        date.fromisoformat(case["window_start"]),
        date.fromisoformat(case["window_end"]),
    )


def case_id(case):
    return f"{case['config']}-{case['today']}"


def item_qty(items, name, unit="шт"):
    return next(i.qty for i in items if i.name == name and i.unit == unit)


@pytest.mark.parametrize("case", SHOPPING_CASES, ids=case_id)
def test_extract_shopping_list_matches_golden(case):
    groups = extract_shopping_list(window_days(case))
    assert [g.category for g in groups] == [g["category"] for g in case["groups"]]
    for group, expected in zip(groups, case["groups"]):
        assert [(i.name, i.unit, i.category) for i in group.items] == [
            (e["name"], e["unit"], group.category) for e in expected["items"]
        ]
        for item, e in zip(group.items, expected["items"]):
            # ceil до 0.1: наименьшее кратное 0.1, не меньшее суммы из JS
            # (допуск — на шум float в JS-сумме, напр. 1.4000000000000001).
            assert Decimal(str(item.qty)) % Decimal("0.1") == 0, item
            assert item.qty >= e["raw_qty"] - 1e-9, item
            assert item.qty - 0.1 < e["raw_qty"] - 1e-9, item


@pytest.mark.parametrize("case", SHOPPING_CASES, ids=case_id)
def test_entries_order_matches_golden(case):
    days = window_days(case)
    entries = [
        *count_portions(d.lunch for d in days).items(),
        *count_portions(d.dinner for d in days).items(),
        *count_portions(d.breakfast for d in days).items(),
    ]
    assert [[r.id, n] for r, n in entries] == case["entries"]


def test_name_order_matches_js_collation():
    assert sorted(NAME_ORDER) == NAME_ORDER


@pytest.mark.parametrize(("qty", "expected"), FMT_QTY_CASES)
def test_format_qty_matches_js(qty, expected):
    assert format_qty(qty) == expected


def test_shared_ingredient_is_summed_once():
    items = aggregate([(R["b1"], 1), (R["b2"], 1)])
    bread = [i for i in items if i.name == "хлеб цельнозерновой"]
    assert [(i.qty, i.unit) for i in bread] == [(4, "кусок")]


def test_fractional_pieces_are_summed():
    items = aggregate([(R["b2"], 1), (R["d1"], 1)])
    assert item_qty(items, "авокадо") == 0.8  # 0.5 + 0.25 = 0.75 → вверх


def test_exact_sum_before_ceil():
    # 0.1 * 3 в float = 0.30000000000000004; ceil по float дал бы 0.4
    assert item_qty(aggregate([(R["b2"], 3)]), "лимон") == 0.3


def test_ceil_rounds_up():
    assert item_qty(aggregate([(R["d1"], 1)]), "авокадо") == 0.3  # 0.25 → 0.3


def test_same_name_different_units_stay_separate():
    items = aggregate([(R["l1"], 1), (R["d3"], 1)])
    assert sorted((i.qty, i.unit) for i in items if i.name == "цукини") == [
        (0.5, "шт"),
        (80, "г"),
    ]
