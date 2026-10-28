"""Действия над каталогом (5.4): правила правки рецептов, слотов и справочника.

С 8.4b правка рецепта может менять и состав позиций цикла (plan_for):
новый обед/ужин раскладывается сам, архивный убирается — с цикла first.

Чистые функции: каталог и запрос → что записать. Ошибки собираются все
сразу (форма рецепта показывает их у полей); loc — путь к полю в теле
запроса. Ккал и белок не принимаются — их считает build_catalog (5.1).
"""

from collections.abc import Iterable
from dataclasses import dataclass, replace

from meal_planner.core.catalog import spec_of
from meal_planner.core.models import (
    Catalog,
    CatalogIngredient,
    Ingredient,
    MealKind,
    RecipeSpec,
    RecipeStatus,
    RecipeStorage,
    SlotVersion,
)
from meal_planner.core.nutrition import nutrition
from meal_planner.core.scheduling import ROTATION_BREAKFASTS
from meal_planner.core.shopping import CATEGORY_ORDER
from meal_planner.core.slot_pool import (
    FISH_MESSAGE,
    KIND_WHAT,
    breaks_fish_rule,
    catalog_fish,
    place_recipe,
    remove_recipe,
    replace_slot,
)

UNITS = ("г", "мл", "шт", "ст.л.", "зубчик")
WEIGHT_UNITS = ("г", "мл")  # граммы в единице не нужны: 1:1
ID_PREFIX = {MealKind.BREAKFAST: "b", MealKind.LUNCH: "l", MealKind.DINNER: "d"}
STORAGE_LABELS = {
    "keep": "Как хранить",
    "pan": "Разогрев на сковороде",
    "micro": "Разогрев в микроволновке",
}

Loc = tuple[str | int, ...]


@dataclass(frozen=True, slots=True)
class FieldError:
    loc: Loc
    message: str


class CatalogError(ValueError):
    """Действие с каталогом невыполнимо (API отвечает 422 со всеми ошибками)."""

    def __init__(self, errors: Iterable[FieldError]):
        self.errors = tuple(errors)
        super().__init__("; ".join(e.message for e in self.errors))


@dataclass(frozen=True, slots=True)
class RecipeLineInput:
    ingredient_id: int
    qty: float


@dataclass(frozen=True, slots=True)
class RecipeInput:
    id: str | None  # None — новый рецепт, id выдаёт сервер
    name: str
    kind: MealKind
    ingredients: tuple[RecipeLineInput, ...]
    steps: tuple[str, ...]
    storage: RecipeStorage | None
    is_fish: bool = False


@dataclass(frozen=True, slots=True)
class RecipeChange:
    """Что записать при правке рецепта: сам рецепт и состав позиций (8.4b)."""

    recipe: RecipeSpec
    slot_versions: tuple[SlotVersion, ...]


@dataclass(frozen=True, slots=True)
class IngredientInput:
    id: int | None  # None — новый ингредиент
    name: str
    unit: str
    category: str
    kcal_100g: float | None  # None — поле формы пустое: ошибка у поля
    protein_100g: float | None
    source: str | None = None
    grams_per_unit: float | None = None
    store_piece: float | None = None


def next_recipe_id(catalog: Catalog, kind: MealKind) -> str:
    # Номера не переиспользуются: архивные рецепты тоже в каталоге.
    prefix = ID_PREFIX[kind]
    numbers = [int(rid[1:]) for rid in catalog.recipes if rid.startswith(prefix)]
    return f"{prefix}{max(numbers, default=0) + 1}"


def used_in(catalog: Catalog, ingredient_name: str) -> list[str]:
    """Id рецептов (в том числе архивных) с этим ингредиентом."""
    return [
        r.id
        for r in catalog.recipes.values()
        if any(i.name == ingredient_name for i in r.ingredients)
    ]


def _text(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def save_recipe(catalog: Catalog, recipe: RecipeInput) -> RecipeSpec:
    errors: list[FieldError] = []
    name = _text(recipe.name)
    if name is None:
        errors.append(FieldError(("name",), "Название не может быть пустым"))

    status: RecipeStatus = "active"
    if recipe.id is None:
        rid = next_recipe_id(catalog, recipe.kind)
    else:
        rid = recipe.id
        existing = catalog.recipes.get(rid)
        if existing is None:
            raise CatalogError([FieldError(("id",), f"Рецепта {rid} нет")])
        if existing.kind != recipe.kind:
            errors.append(
                FieldError(("kind",), "Тип блюда не меняется — создайте новый рецепт")
            )
        status = existing.status

    by_id = {i.id: i for i in catalog.ingredients.values()}
    lines = []
    seen: set[int] = set()
    if not recipe.ingredients:
        errors.append(FieldError(("ingredients",), "Нужен хотя бы один ингредиент"))
    for n, line in enumerate(recipe.ingredients):
        ingredient = by_id.get(line.ingredient_id)
        if ingredient is None:
            errors.append(
                FieldError(
                    ("ingredients", n, "ingredient_id"),
                    "Такого ингредиента нет в справочнике",
                )
            )
        elif line.ingredient_id in seen:
            errors.append(
                FieldError(
                    ("ingredients", n, "ingredient_id"),
                    f"«{ingredient.name}» уже есть в рецепте",
                )
            )
        if line.qty <= 0:
            errors.append(
                FieldError(
                    ("ingredients", n, "qty"), "Количество должно быть больше нуля"
                )
            )
        seen.add(line.ingredient_id)
        if ingredient is not None:
            lines.append(
                Ingredient(
                    ingredient.name, line.qty, ingredient.unit, ingredient.category
                )
            )

    steps = [_text(step) for step in recipe.steps]
    if not steps:
        errors.append(FieldError(("steps",), "Нужен хотя бы один шаг"))
    errors.extend(
        FieldError(("steps", n), "Шаг не может быть пустым")
        for n, step in enumerate(steps)
        if step is None
    )

    storage = recipe.storage
    if recipe.kind == MealKind.BREAKFAST:
        if storage is not None:
            errors.append(FieldError(("storage",), "У завтрака хранения нет"))
    elif storage is None:
        errors.append(
            FieldError(
                ("storage",),
                "У обеда и ужина нужно хранение и разогрев на сковороде и в"
                " микроволновке",
            )
        )
    else:
        cleaned = {f: _text(getattr(storage, f)) for f in STORAGE_LABELS}
        errors.extend(
            FieldError(("storage", f), f"{label}: заполните поле")
            for f, label in STORAGE_LABELS.items()
            if cleaned[f] is None
        )
        storage = RecipeStorage(
            keep=cleaned["keep"] or "",
            pan=cleaned["pan"] or "",
            micro=cleaned["micro"] or "",
            after=_text(storage.after),
        )

    if errors:
        raise CatalogError(errors)
    return RecipeSpec(
        id=rid,
        name=name or "",
        kind=recipe.kind,
        ingredients=tuple(lines),
        steps=tuple(step or "" for step in steps),
        storage=storage,
        is_fish=recipe.is_fish,
        status=status,
    )


def preview_nutrition(
    catalog: Catalog, lines: Iterable[RecipeLineInput]
) -> tuple[int, int]:
    """Ккал и белок черновика (5.5) — тот же расчёт, что при сохранении.

    Недописанные строки (ингредиент не выбран, количество ≤ 0) пропускаются:
    для предпросмотра это не ошибка, её покажет save-recipe.
    """
    by_id = {i.id: i for i in catalog.ingredients.values()}
    counted = [
        Ingredient(ingredient.name, line.qty, ingredient.unit, ingredient.category)
        for line in lines
        if line.qty > 0 and (ingredient := by_id.get(line.ingredient_id)) is not None
    ]
    return nutrition(catalog.ingredients, counted)


def _existing(catalog: Catalog, recipe_id: str) -> RecipeSpec:
    recipe = catalog.recipes.get(recipe_id)
    if recipe is None:
        raise CatalogError([FieldError(("id",), f"Рецепта {recipe_id} нет")])
    return spec_of(recipe)


def archive_recipe(catalog: Catalog, recipe_id: str) -> RecipeSpec:
    """В архив: из пула пропадает, но не удаляется (5.1). Повтор — не ошибка.

    Из позиций цикла рецепт убирает plan_for (8.4b).
    """
    spec = _existing(catalog, recipe_id)
    if recipe_id in ROTATION_BREAKFASTS:
        raise CatalogError(
            [FieldError(("id",), f"«{spec.name}» — завтрак в ротации, в архив нельзя")]
        )
    return replace(spec, status="archived")


def restore_recipe(catalog: Catalog, recipe_id: str) -> RecipeSpec:
    return replace(_existing(catalog, recipe_id), status="active")


def plan_for(catalog: Catalog, spec: RecipeSpec, first: int) -> RecipeChange:
    """Состав позиций после сохранения spec (8.4b) — с цикла first.

    Новый или вернувшийся из архива обед/ужин встаёт в позицию сам; ушедший
    в архив убирается из позиций, если он там не единственный своего типа.
    Рецепт, ставший рыбным, не должен дать лишнюю рыбную позицию.
    """
    versions = catalog.slot_versions
    if spec.kind == MealKind.BREAKFAST:
        return RecipeChange(spec, versions)
    existing = catalog.recipes.get(spec.id)
    fish_before = catalog_fish(catalog)

    def fish_after(rid: str) -> bool:
        return spec.is_fish if rid == spec.id else fish_before(rid)

    if spec.status == "archived":
        versions, only = remove_recipe(catalog, versions, spec.id, first)
        if only:
            what = KIND_WHAT[spec.kind]
            raise CatalogError(
                [
                    FieldError(
                        ("id",),
                        f"«{spec.name}» — единственный {what} позиции"
                        f" №{only[0]} — сначала поставьте туда другой",
                    )
                ]
            )
    elif existing is None or existing.status == "archived":
        versions = place_recipe(catalog, versions, spec, first)
    if breaks_fish_rule(
        catalog.slot_versions, versions, first, fish_before, fish_after
    ):
        raise CatalogError([FieldError(("is_fish",), FISH_MESSAGE)])
    return RecipeChange(spec, versions)


def set_cycle_slot(
    catalog: Catalog,
    slot: int,
    lunch_ids: tuple[str, ...],
    dinner_ids: tuple[str, ...],
    first: int,
) -> tuple[SlotVersion, ...]:
    """Состав позиции целиком (8.4b) — с цикла first; списки ≥ 1, без повторов."""
    errors = []
    count = catalog.slot_count
    if not 1 <= slot <= count:
        errors.append(FieldError(("slot",), f"Позиция цикла — от 1 до {count}"))
    for field, ids, kind in (
        ("lunch_ids", lunch_ids, MealKind.LUNCH),
        ("dinner_ids", dinner_ids, MealKind.DINNER),
    ):
        what = KIND_WHAT[kind]
        if not ids:
            errors.append(FieldError((field,), f"Нужен хотя бы один {what}"))
        for n, rid in enumerate(ids):
            recipe = catalog.recipes.get(rid)
            if recipe is None:
                errors.append(FieldError((field, n), f"Рецепта {rid} нет"))
            elif recipe.kind != kind:
                errors.append(FieldError((field, n), f"«{recipe.name}» — не {what}"))
            elif recipe.status != "active":
                errors.append(
                    FieldError(
                        (field, n), f"«{recipe.name}» в архиве — сначала верните его"
                    )
                )
            elif rid in ids[:n]:
                errors.append(
                    FieldError((field, n), f"«{recipe.name}» уже в этой позиции")
                )
    if errors:
        raise CatalogError(errors)
    versions = replace_slot(catalog.slot_versions, slot, first, lunch_ids, dinner_ids)
    fish = catalog_fish(catalog)
    if breaks_fish_rule(catalog.slot_versions, versions, first, fish, fish):
        raise CatalogError([FieldError(("slot",), FISH_MESSAGE)])
    return versions


def save_ingredient(catalog: Catalog, ingredient: IngredientInput) -> CatalogIngredient:
    errors = []
    by_id = {i.id: i for i in catalog.ingredients.values()}
    existing = None
    if ingredient.id is None:
        iid = max(by_id, default=0) + 1
    else:
        iid = ingredient.id
        existing = by_id.get(iid)
        if existing is None:
            raise CatalogError([FieldError(("id",), "Такого ингредиента нет")])

    name = _text(ingredient.name)
    if name is None:
        errors.append(FieldError(("name",), "Название не может быть пустым"))
    elif any(
        i.name.casefold() == name.casefold() and i.id != iid for i in by_id.values()
    ):
        errors.append(FieldError(("name",), f"Ингредиент «{name}» уже есть"))

    unit = ingredient.unit
    if unit not in UNITS:
        errors.append(FieldError(("unit",), f"Единица — одна из: {', '.join(UNITS)}"))
    elif existing is not None and unit != existing.unit:
        recipes = used_in(catalog, existing.name)
        if recipes:
            errors.append(
                FieldError(
                    ("unit",),
                    f"Единицу нельзя менять: «{existing.name}» есть в рецептах"
                    f" ({', '.join(recipes)})",
                )
            )
    if ingredient.category not in CATEGORY_ORDER:
        errors.append(
            FieldError(
                ("category",), f"Категория — одна из: {', '.join(CATEGORY_ORDER)}"
            )
        )
    kcal, protein = ingredient.kcal_100g, ingredient.protein_100g
    if kcal is None:
        errors.append(FieldError(("kcal_100g",), "Укажите ккал на 100 г"))
    elif kcal < 0:
        errors.append(
            FieldError(("kcal_100g",), "Ккал на 100 г не может быть меньше нуля")
        )
    if protein is None:
        errors.append(FieldError(("protein_100g",), "Укажите белок на 100 г"))
    elif not 0 <= protein <= 100:
        errors.append(FieldError(("protein_100g",), "Белок на 100 г — от 0 до 100"))

    grams_per_unit = None if unit in WEIGHT_UNITS else ingredient.grams_per_unit
    if unit in UNITS and unit not in WEIGHT_UNITS and not (grams_per_unit or 0) > 0:
        errors.append(
            FieldError(
                ("grams_per_unit",),
                f"Сколько граммов в «{unit}» — нужно число больше нуля",
            )
        )
    if ingredient.store_piece is not None and ingredient.store_piece <= 0:
        errors.append(
            FieldError(("store_piece",), "Штука в магазине — больше нуля или пусто")
        )

    if errors:
        raise CatalogError(errors)
    return CatalogIngredient(
        id=iid,
        name=name or "",
        unit=unit,
        category=ingredient.category,
        kcal_100g=kcal or 0,
        protein_100g=protein or 0,
        source=_text(ingredient.source),
        grams_per_unit=grams_per_unit,
        store_piece=ingredient.store_piece,
    )
