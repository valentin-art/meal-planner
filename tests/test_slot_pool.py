"""Варианты у позиций цикла (8.4b): ротация, версии «с цикла F», авторасклад.

Новое правило, а не порт JSX: ожидания записаны как правило. С одним
вариантом на позицию план прежний — это держат golden-тесты.
"""

from dataclasses import replace
from datetime import date, timedelta
from itertools import combinations

import pytest

from meal_planner.core import catalog_actions as ca
from meal_planner.core.catalog import build_catalog
from meal_planner.core.catalog_actions import CatalogError
from meal_planner.core.cooking import batch_dishes
from meal_planner.core.models import (
    Catalog,
    CycleShuffle,
    Settings,
    SlotVersion,
)
from meal_planner.core.planner import next_shopping_trip, recompute
from meal_planner.core.recipes_data import RECIPE_SPECS
from meal_planner.core.scheduling import (
    build_schedule,
    cook_weekdays,
    cook_window,
    first_free_cycle,
    first_free_day,
    today_position,
)
from meal_planner.core.seed import SEED_CATALOG, seed_ingredients
from meal_planner.core.slot_pool import FISH_MESSAGE, fish_slots

DAY = timedelta(days=1)
# Ср/Сб, якорь — чт 05.11: окна чт–сб (3 дня) и вс–ср (4 дня).
BASE = Settings(date(2026, 11, 5), 2, 3, 6, "same")
# Обеды и ужины сверх сида — копии с новыми id.
L6 = replace(RECIPE_SPECS["l1"], id="l6", name="Плов с курицей")
L7 = replace(RECIPE_SPECS["l3"], id="l7", name="Треска с рисом")  # рыба
D6 = replace(RECIPE_SPECS["d1"], id="d6", name="Салат с индейкой")


def catalog_with(*versions: SlotVersion, extra=(L6, L7, D6)) -> Catalog:
    """Сид плюс рецепты extra; versions заменяют версии своих позиций."""
    replaced = {v.slot for v in versions}
    kept = [v for v in SEED_CATALOG.slot_versions if v.slot not in replaced]
    return build_catalog(
        seed_ingredients(), [*RECIPE_SPECS.values(), *extra], [*kept, *versions]
    )


def lunches(catalog: Catalog, k: int) -> tuple[str, ...]:
    return [v for v in catalog.slot_versions if v.slot == k][-1].lunch_ids


def errors(call) -> list[tuple[tuple, str]]:
    with pytest.raises(CatalogError) as e:
        call()
    return [(err.loc, err.message) for err in e.value.errors]


# --- ротация ---


def test_variants_rotate_per_cycle_independently():
    catalog = catalog_with(SlotVersion(1, 0, ("l1", "l6"), ("d1", "d6", "d4")))
    assert [
        (catalog.slot_at(1, n).lunch_id, catalog.slot_at(1, n).dinner_id)
        for n in range(6)
    ] == [
        ("l1", "d1"),
        ("l6", "d6"),
        ("l1", "d4"),
        ("l6", "d1"),
        ("l1", "d6"),
        ("l6", "d4"),
    ]


def test_schedule_takes_variant_of_the_days_cycle():
    # Позиция 1 — раз в цикл (в партии до 4 дней позиции разные): обеды
    # чередуются, начиная с цикла 0.
    catalog = catalog_with(SlotVersion(1, 0, ("l1", "l6"), ("d1",)))
    days = build_schedule(catalog, BASE, date(2026, 11, 5), date(2026, 12, 31))
    seen = [d.lunch.id for d in days if d.cycle_index == 1 and d.lunch]
    assert len(seen) == 12
    assert seen == ["l1", "l6"] * 6


def test_one_variant_is_the_seed_plan():
    catalog = catalog_with(SlotVersion(1, 0, ("l1",), ("d1",)))
    window = date(2026, 11, 1), date(2027, 1, 31)
    assert build_schedule(catalog, BASE, *window) == build_schedule(
        SEED_CATALOG, BASE, *window
    )


# --- версии с первого некупленного цикла ---


@pytest.mark.parametrize("timing", ["same", "before"])
@pytest.mark.parametrize("today_offset", range(14))
def test_slot_change_keeps_bought_days_and_trip(timing, today_offset):
    today = date(2026, 11, 3) + timedelta(days=today_offset)
    settings = replace(BASE, shopping_timing=timing)
    first = first_free_cycle(settings, today)
    before = catalog_with()
    after = replace(
        before,
        slot_versions=ca.set_cycle_slot(before, 3, ("l6",), ("d6",), first),
    )
    state = recompute(after, settings, today)
    # До старта цикла (правка с цикла 0) купленного нет — даты нет.
    changes_from = state.slots[2].changes_from or settings.meal_anchor_date
    assert changes_from >= first_free_day(settings, today) > today
    assert build_schedule(after, settings, today, changes_from - DAY) == (
        build_schedule(before, settings, today, changes_from - DAY)
    )
    trip = next_shopping_trip(before, settings, today)
    if today >= trip.shopping_date:
        assert next_shopping_trip(after, settings, today) == trip


def test_later_change_replaces_pending_versions():
    catalog = catalog_with()
    v3 = ca.set_cycle_slot(catalog, 1, ("l6",), ("d1",), 3)
    v4 = ca.set_cycle_slot(
        replace(catalog, slot_versions=v3), 1, ("l1", "l6"), ("d1",), 4
    )
    assert [v for v in v4 if v.slot == 1] == [
        SlotVersion(1, 0, ("l1",), ("d1",)),
        SlotVersion(1, 3, ("l6",), ("d1",)),
        SlotVersion(1, 4, ("l1", "l6"), ("d1",)),
    ]
    v2 = ca.set_cycle_slot(replace(catalog, slot_versions=v4), 1, ("l6",), ("d1",), 2)
    assert [v for v in v2 if v.slot == 1] == [
        SlotVersion(1, 0, ("l1",), ("d1",)),
        SlotVersion(1, 2, ("l6",), ("d1",)),
    ]


def test_unchanged_slot_adds_no_version():
    catalog = catalog_with()
    versions = ca.set_cycle_slot(catalog, 1, ("l1",), ("d1",), 3)
    assert versions == catalog.slot_versions
    state = recompute(catalog, BASE, date(2026, 11, 9))
    assert all(s.changes_from is None for s in state.slots)


# --- авторасклад ---


def test_new_lunch_goes_to_fewest_variants_then_lowest_slot():
    # Нового рецепта в каталоге ещё нет — plan_for его раскладывает.
    placed = ca.plan_for(catalog_with(extra=()), L6, 2).slot_versions
    assert [v for v in placed if v.slot == 1][-1] == SlotVersion(
        1, 2, ("l1", "l6"), ("d1",)
    )
    second = replace(L6, id="l8", name="Ещё обед")
    with_l6 = replace(catalog_with(extra=(L6,)), slot_versions=placed)
    again = ca.plan_for(with_l6, second, 2).slot_versions
    assert [v for v in again if v.slot == 2][-1].lunch_ids == ("l2", "l8")
    dinner = ca.plan_for(catalog_with(extra=()), D6, 2).slot_versions
    assert [v for v in dinner if v.slot == 1][-1].dinner_ids == ("d1", "d6")


def test_fish_goes_only_to_fish_slots_when_three_are_fish():
    catalog = catalog_with(extra=())
    fish = lambda rid: catalog.recipes[rid].is_fish
    assert fish_slots(catalog.slot_versions, 0, fish) == {2, 3, 5}
    placed = ca.plan_for(catalog, L7, 2).slot_versions
    assert [v for v in placed if v.slot == 2][-1].lunch_ids == ("l2", "l7")


def test_breakfast_is_not_placed():
    b4 = replace(RECIPE_SPECS["b2"], id="b4", name="Овсянка")
    catalog = catalog_with(extra=(b4,))
    assert ca.plan_for(catalog, b4, 2).slot_versions == catalog.slot_versions


def test_restore_places_again():
    catalog = catalog_with(extra=(replace(L6, status="archived"),))
    restored = ca.restore_recipe(catalog, "l6")
    versions = ca.plan_for(catalog, restored, 2).slot_versions
    assert lunches(replace(catalog, slot_versions=versions), 1) == ("l1", "l6")


def test_edit_of_placed_recipe_does_not_place_again():
    catalog = catalog_with(SlotVersion(1, 0, ("l1", "l6"), ("d1",)))
    edited = replace(L6, name="Плов")
    assert ca.plan_for(catalog, edited, 2).slot_versions == catalog.slot_versions


# --- правило рыбы ---


def test_fourth_fish_slot_is_refused():
    catalog = catalog_with()
    assert errors(lambda: ca.set_cycle_slot(catalog, 1, ("l1",), ("d2",), 2)) == [
        (("slot",), FISH_MESSAGE)
    ]
    # В рыбную позицию — можно.
    ca.set_cycle_slot(catalog, 3, ("l3", "l7"), ("d3",), 2)


def test_recipe_turning_fish_is_refused():
    catalog = catalog_with()
    fishy_l1 = replace(RECIPE_SPECS["l1"], is_fish=True)
    assert errors(lambda: ca.plan_for(catalog, fishy_l1, 2)) == [
        (("is_fish",), FISH_MESSAGE)
    ]
    # l2 — в позиции 2, она уже рыбная (ужин d2).
    ca.plan_for(catalog, replace(RECIPE_SPECS["l2"], is_fish=True), 2)


def test_existing_violation_is_not_an_error():
    # До 8.4b четвёртую рыбную позицию можно было поставить вручную — правка
    # другой позиции из-за этого не отклоняется.
    catalog = catalog_with(SlotVersion(1, 0, ("l1",), ("d2",)))
    ca.set_cycle_slot(catalog, 4, ("l4", "l6"), ("d4",), 2)


@pytest.mark.parametrize("weekdays", list(combinations(range(1, 8), 2)), ids=str)
def test_fish_days_stay_in_first_three_of_a_batch(weekdays):
    # Рыбный обед l7 разложен сам, порядок перемешан (8.4a): в партии до
    # 4 дней — не больше 3 рыбных дней, и они первые.
    versions = ca.plan_for(catalog_with(extra=()), L7, 1).slot_versions
    with_l7 = replace(catalog_with(extra=(L7,)), slot_versions=versions)
    versions = ca.plan_for(with_l7, L6, 1).slot_versions
    catalog = replace(catalog_with(extra=(L7, L6)), slot_versions=versions)
    settings = replace(
        BASE,
        cook_weekday1=weekdays[0],
        cook_weekday2=weekdays[1],
        cycle_shuffles=(CycleShuffle(0, 11),),
    )
    d = date(2026, 11, 5)
    while d < date(2027, 2, 1):
        if d.isoweekday() in cook_weekdays(settings):
            days = build_schedule(catalog, settings, *cook_window(settings, d))
            fish = [
                day.lunch.is_fish or day.dinner.is_fish
                for day in days
                if day.lunch and day.dinner
            ]
            assert len(fish) == len(days)
            if len(days) <= 4:
                assert sum(fish) <= 3
            assert fish == sorted(fish, reverse=True)
        d += DAY


# --- архив ---


def test_archive_removes_from_first_cycle():
    catalog = catalog_with(SlotVersion(1, 0, ("l1", "l6"), ("d1",)))
    archived = ca.archive_recipe(catalog, "l6")
    versions = ca.plan_for(catalog, archived, 3).slot_versions
    assert [v for v in versions if v.slot == 1] == [
        SlotVersion(1, 0, ("l1", "l6"), ("d1",)),
        SlotVersion(1, 3, ("l1",), ("d1",)),
    ]


def test_archive_from_pending_version_too():
    # Рецепт добавлен с цикла 4 — архив с цикла 2 убирает и эту версию.
    catalog = catalog_with(
        SlotVersion(1, 0, ("l1",), ("d1",)), SlotVersion(1, 4, ("l1", "l6"), ("d1",))
    )
    archived = ca.archive_recipe(catalog, "l6")
    versions = ca.plan_for(catalog, archived, 2).slot_versions
    assert [v for v in versions if v.slot == 1] == [SlotVersion(1, 0, ("l1",), ("d1",))]


# --- что готовить ---


def test_same_slot_from_two_cycles_is_two_dishes():
    # Готовка раз в неделю (вс): окно 7 дней, позиция встречается из двух
    # циклов — с разными вариантами это два блюда.
    catalog = catalog_with(SlotVersion(1, 0, ("l1", "l6"), ("d1",)))
    settings = Settings(date(2026, 11, 2), 1, 7, None, "same")
    found = False
    d = date(2026, 11, 8)
    while d < date(2027, 1, 31):
        trip = next_shopping_trip(catalog, settings, d - DAY)
        pairs = [(c.lunch.id, c.dinner.id) for c in trip.cook_items]
        assert len(pairs) == len(set(pairs))
        days = build_schedule(catalog, settings, trip.window_start, trip.window_end)
        assert sum(c.portions for c in trip.cook_items) == len(days)
        if {("l1", "d1"), ("l6", "d1")} <= set(pairs):
            found = True
            dishes = {
                r.id: n for r, n in batch_dishes(catalog, settings, trip.cook_date)
            }
            assert dishes["d1"] == sum(
                c.portions for c in trip.cook_items if c.dinner.id == "d1"
            )
        d += 7 * DAY
    assert found


def test_today_position_shows_variant_of_today():
    catalog = catalog_with(SlotVersion(1, 0, ("l1", "l6"), ("d1",)))
    today = date(2026, 11, 20)
    for o in today_position(catalog, today, BASE.meal_anchor_date, (3, 6)).options[1:]:
        day = build_schedule(
            catalog, replace(BASE, meal_anchor_date=o.anchor), today, today
        )[0]
        assert (o.lunch, o.dinner) == (day.lunch, day.dinner)
