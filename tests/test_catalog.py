"""5.3: core/ берёт рецепты, справочник и слоты из каталога, а не из констант."""

from dataclasses import replace
from datetime import date

from golden_scheduling import SCENARIOS
from scenario_settings import settings_of

from meal_planner.core.catalog import build_catalog
from meal_planner.core.models import MealKind, RecipeSpec, SlotVersion
from meal_planner.core.planner import next_shopping_trip
from meal_planner.core.recipes_data import RECIPE_SPECS
from meal_planner.core.scheduling import build_schedule
from meal_planner.core.seed import SEED_CATALOG, seed_ingredients, seed_slot_versions
from meal_planner.db.repository import Repository

DEFAULT_2X = settings_of(next(s for s in SCENARIOS if s["name"] == "default_2x"))


def with_slots(slots: list[tuple[str, str]]):
    return build_catalog(
        seed_ingredients(),
        RECIPE_SPECS.values(),
        [
            SlotVersion(k, 0, (lunch,), (dinner,))
            for k, (lunch, dinner) in enumerate(slots, 1)
        ],
    )


def test_db_catalog_equals_seed(tmp_path):
    # Без правок в БД план и покупки те же, что на константах (готовность фазы).
    assert Repository(tmp_path / "db.sqlite").get_catalog() == SEED_CATALOG


def test_order_is_kind_then_number():
    assert list(SEED_CATALOG.recipes) == [
        "b1",
        "b2",
        "b3",
        *(f"l{i}" for i in range(1, 6)),
        *(f"d{i}" for i in range(1, 6)),
    ]
    l10 = replace(RECIPE_SPECS["l1"], id="l10")
    specs = [l10, *reversed(list(RECIPE_SPECS.values()))]
    catalog = build_catalog(seed_ingredients(), specs, seed_slot_versions())
    assert list(catalog.recipes)[-6:] == ["l10", "d1", "d2", "d3", "d4", "d5"]


def test_kcal_follow_ingredient_table():
    eggs = [
        replace(i, kcal_100g=0, protein_100g=0) if i.name == "яйца" else i
        for i in seed_ingredients()
    ]
    catalog = build_catalog(eggs, RECIPE_SPECS.values(), seed_slot_versions())
    # b1 без яиц: масло 14.2 г и хлеб 60 г.
    assert (catalog.recipes["b1"].kcal, catalog.recipes["b1"].protein) == (255, 8)
    assert SEED_CATALOG.recipes["b1"].kcal == 540


def test_schedule_follows_slots():
    # Слоты 1 и 4 — оба без рыбы: порядок позиций прежний, блюда — из слотов.
    swapped = with_slots(
        [("l4", "d4"), ("l2", "d2"), ("l3", "d3"), ("l1", "d1"), ("l5", "d5")]
    )
    days = date(2026, 11, 8), date(2026, 11, 20)
    seed = build_schedule(SEED_CATALOG, DEFAULT_2X, *days)
    moved = build_schedule(swapped, DEFAULT_2X, *days)
    other = {1: 4, 4: 1}
    for a, b in zip(seed, moved, strict=True):
        assert a.cycle_index == b.cycle_index
        assert a.cycle_index is not None and b.lunch and b.dinner
        k = other.get(a.cycle_index, a.cycle_index)
        assert (b.lunch.id, b.dinner.id) == (f"l{k}", f"d{k}")


def test_fish_order_follows_slots():
    # Рыбный ужин d2 в слоте 1 вместо мясного d1: позиция 1 теперь рыбная и
    # в партии съедается раньше позиции 2 (6.6). Окно Вс 15 – Ср 18 ноября.
    fish_first = with_slots(
        [("l1", "d2"), ("l2", "d1"), ("l3", "d3"), ("l4", "d4"), ("l5", "d5")]
    )
    window = date(2026, 11, 15), date(2026, 11, 18)
    seed = [d.cycle_index for d in build_schedule(SEED_CATALOG, DEFAULT_2X, *window)]
    moved = [d.cycle_index for d in build_schedule(fish_first, DEFAULT_2X, *window)]
    assert (seed, moved) == ([3, 2, 1, 4], [3, 1, 2, 4])


def test_same_lunch_in_two_slots_cooks_per_slot():
    # Один рецепт в нескольких слотах (5.1): ужин — из своего слота, а не по
    # первому вхождению обеда.
    twice = with_slots(
        [("l1", "d1"), ("l1", "d2"), ("l3", "d3"), ("l4", "d4"), ("l5", "d5")]
    )
    trip = next_shopping_trip(twice, DEFAULT_2X, date(2026, 11, 7))
    pairs = [(c.lunch.id, c.dinner.id) for c in trip.cook_items]
    seed = next_shopping_trip(SEED_CATALOG, DEFAULT_2X, date(2026, 11, 7))
    expected = [
        ("l1", c.dinner.id) if c.lunch.id == "l2" else (c.lunch.id, c.dinner.id)
        for c in seed.cook_items
    ]
    assert pairs == expected
    assert [c.portions for c in trip.cook_items] == [
        c.portions for c in seed.cook_items
    ]


def test_breakfasts_stay_b1_b2():
    kinds = {r.kind for r in SEED_CATALOG.recipes.values()}
    assert kinds == set(MealKind)
    days = build_schedule(
        SEED_CATALOG, DEFAULT_2X, date(2026, 11, 5), date(2026, 11, 30)
    )
    assert {d.breakfast.id for d in days if d.breakfast} == {"b1", "b2"}


def test_spec_has_no_nutrition():
    assert not hasattr(RECIPE_SPECS["b1"], "kcal")
    assert isinstance(RECIPE_SPECS["b1"], RecipeSpec)
