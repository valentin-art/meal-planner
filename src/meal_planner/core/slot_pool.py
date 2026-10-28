"""Варианты у позиций цикла (8.4b): правка состава версиями «с цикла F».

У позиции может быть несколько обедов и ужинов; цикл n берёт вариант
n % len (Catalog.slot_at). Любая правка — вручную, при создании или архивации
рецепта — пишется версией с первого некупленного цикла F: циклы до F не
меняются, купленное тоже.

Рыба — не больше чем в 3 позициях из 5: в партии до 4 дней позиции разные
(8.4a), значит рыбных дней не больше 3, и рыба остаётся в первых трёх днях.
"""

from collections.abc import Callable

from meal_planner.core.models import (
    Catalog,
    MealKind,
    RecipeSpec,
    SlotVersion,
    version_at,
)

MAX_FISH_SLOTS = 3
FISH_MESSAGE = (
    f"Рыба — не больше чем в {MAX_FISH_SLOTS} позициях цикла из 5: иначе в партии"
    " на 4 дня рыба попадёт на 4-й день"
)
KIND_WHAT = {MealKind.LUNCH: "обед", MealKind.DINNER: "ужин"}

Versions = tuple[SlotVersion, ...]
IsFish = Callable[[str], bool]
Lists = tuple[tuple[str, ...], tuple[str, ...]]


def slot_numbers(versions: Versions) -> list[int]:
    return sorted({v.slot for v in versions})


def latest(versions: Versions, k: int) -> SlotVersion:
    """Последняя версия позиции — то, что остаётся после всех правок."""
    return [v for v in versions if v.slot == k][-1]


def ids_of(version: SlotVersion, kind: MealKind) -> tuple[str, ...]:
    return version.lunch_ids if kind == MealKind.LUNCH else version.dinner_ids


def with_ids(version: SlotVersion, kind: MealKind, ids: tuple[str, ...]) -> Lists:
    if kind == MealKind.LUNCH:
        return ids, version.dinner_ids
    return version.lunch_ids, ids


def versions_from(versions: Versions, k: int, first: int) -> list[SlotVersion]:
    """Версии позиции k, действующие с цикла first: текущая и более поздние."""
    return [version_at(versions, k, first)] + [
        v for v in versions if v.slot == k and v.from_cycle > first
    ]


def compact(versions: list[SlotVersion]) -> Versions:
    """По позиции и from_cycle; версия, не меняющая состав, не нужна."""
    result: list[SlotVersion] = []
    for v in sorted(versions, key=lambda v: (v.slot, v.from_cycle)):
        prev = result[-1] if result and result[-1].slot == v.slot else None
        if prev and (prev.lunch_ids, prev.dinner_ids) == (v.lunch_ids, v.dinner_ids):
            continue
        result.append(v)
    return tuple(result)


def edit_slot(
    versions: Versions, k: int, first: int, edit: Callable[[SlotVersion], Lists]
) -> Versions:
    """Меняет состав позиции k с цикла first — и в более поздних версиях."""
    edited = [
        SlotVersion(k, max(v.from_cycle, first), *edit(v))
        for v in versions_from(versions, k, first)
    ]
    kept = [v for v in versions if not (v.slot == k and v.from_cycle >= first)]
    return compact(kept + edited)


def replace_slot(
    versions: Versions,
    k: int,
    first: int,
    lunch_ids: tuple[str, ...],
    dinner_ids: tuple[str, ...],
) -> Versions:
    """Состав позиции целиком с цикла first; более поздние версии отменяются."""
    kept = [v for v in versions if not (v.slot == k and v.from_cycle >= first)]
    return compact([*kept, SlotVersion(k, first, lunch_ids, dinner_ids)])


def in_plan(versions: Versions, recipe_id: str, first: int) -> list[int]:
    """Позиции, где рецепт стоит в каком-либо цикле с first."""
    return [
        k
        for k in slot_numbers(versions)
        if any(
            recipe_id in v.lunch_ids + v.dinner_ids
            for v in versions_from(versions, k, first)
        )
    ]


def fish_slots(versions: Versions, n: int, is_fish: IsFish) -> set[int]:
    """Позиции, где в цикле n хоть один вариант обеда или ужина — рыба."""
    return {
        k
        for k in slot_numbers(versions)
        if any(map(is_fish, (v := version_at(versions, k, n)).lunch_ids + v.dinner_ids))
    }


def breaks_fish_rule(
    before: Versions,
    after: Versions,
    first: int,
    fish_before: IsFish,
    fish_after: IsFish,
) -> bool:
    """Правка добавила рыбную позицию сверх MAX_FISH_SLOTS в каком-то цикле.

    Проверяются циклы, где меняется состав. Уже нарушенное (до 8.4b так
    можно было поставить вручную) правка не ухудшает — это не ошибка.
    """
    cycles = {first} | {v.from_cycle for v in before + after if v.from_cycle > first}
    return any(
        len(fish_slots(after, n, fish_after))
        > max(MAX_FISH_SLOTS, len(fish_slots(before, n, fish_before)))
        for n in cycles
    )


def catalog_fish(catalog: Catalog) -> IsFish:
    return lambda rid: catalog.recipes[rid].is_fish


def place_recipe(
    catalog: Catalog, versions: Versions, recipe: RecipeSpec, first: int
) -> Versions:
    """Авторасклад: новый или вернувшийся обед/ужин — в позицию, где меньше
    всего вариантов этого типа; среди равных — меньший номер. Рыбный — только
    в рыбную позицию, если рыбных уже MAX_FISH_SLOTS."""
    kind = recipe.kind
    fish = catalog_fish(catalog)
    candidates = slot_numbers(versions)
    if recipe.is_fish:
        last = max(v.from_cycle for v in versions)
        fishy = fish_slots(versions, max(last, first), fish)
        if len(fishy) >= MAX_FISH_SLOTS:
            candidates = sorted(fishy)
    k = min(candidates, key=lambda k: (len(ids_of(latest(versions, k), kind)), k))
    return edit_slot(
        versions,
        k,
        first,
        lambda v: with_ids(v, kind, (*ids_of(v, kind), recipe.id)),
    )


def remove_recipe(
    catalog: Catalog, versions: Versions, recipe_id: str, first: int
) -> tuple[Versions, list[int]]:
    """Убирает рецепт из позиций с цикла first.

    Возвращает новые версии и позиции, где он единственный своего типа, —
    оттуда убрать нельзя (версии тогда не меняются).
    """
    kind = catalog.recipes[recipe_id].kind
    only: list[int] = []
    for k in in_plan(versions, recipe_id, first):
        for v in versions_from(versions, k, first):
            if ids_of(v, kind) == (recipe_id,):
                only.append(k)
                break
        else:
            versions = edit_slot(
                versions,
                k,
                first,
                lambda v: with_ids(
                    v, kind, tuple(r for r in ids_of(v, kind) if r != recipe_id)
                ),
            )
    return versions, only
