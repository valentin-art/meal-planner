"""Действия над каталогом (5.4): правила правки рецептов, слотов и справочника.

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
    CycleSlot,
    Ingredient,
    MealKind,
    RecipeSpec,
    RecipeStatus,
    RecipeStorage,
)
from meal_planner.core.scheduling import ROTATION_BREAKFASTS
from meal_planner.core.shopping import CATEGORY_ORDER

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
class IngredientInput:
    id: int | None  # None — новый ингредиент
    name: str
    unit: str
    category: str
    kcal_100g: float
    protein_100g: float
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


def _existing(catalog: Catalog, recipe_id: str) -> RecipeSpec:
    recipe = catalog.recipes.get(recipe_id)
    if recipe is None:
        raise CatalogError([FieldError(("id",), f"Рецепта {recipe_id} нет")])
    return spec_of(recipe)


def archive_recipe(catalog: Catalog, recipe_id: str) -> RecipeSpec:
    """В архив: из пула пропадает, но не удаляется (5.1). Повтор — не ошибка."""
    spec = _existing(catalog, recipe_id)
    for slot in catalog.slots:
        if recipe_id in (slot.lunch_id, slot.dinner_id):
            raise CatalogError(
                [
                    FieldError(
                        ("id",),
                        f"«{spec.name}» стоит в цикле, позиция {slot.slot} —"
                        " сначала замените его",
                    )
                ]
            )
    if recipe_id in ROTATION_BREAKFASTS:
        raise CatalogError(
            [FieldError(("id",), f"«{spec.name}» — завтрак в ротации, в архив нельзя")]
        )
    return replace(spec, status="archived")


def restore_recipe(catalog: Catalog, recipe_id: str) -> RecipeSpec:
    return replace(_existing(catalog, recipe_id), status="active")


def set_cycle_slot(
    catalog: Catalog, slot: int, lunch_id: str, dinner_id: str
) -> CycleSlot:
    errors = []
    if not 1 <= slot <= len(catalog.slots):
        errors.append(
            FieldError(("slot",), f"Позиция цикла — от 1 до {len(catalog.slots)}")
        )
    for field, rid, kind, what in (
        ("lunch_id", lunch_id, MealKind.LUNCH, "обед"),
        ("dinner_id", dinner_id, MealKind.DINNER, "ужин"),
    ):
        recipe = catalog.recipes.get(rid)
        if recipe is None:
            errors.append(FieldError((field,), f"Рецепта {rid} нет"))
        elif recipe.kind != kind:
            errors.append(FieldError((field,), f"«{recipe.name}» — не {what}"))
        elif recipe.status != "active":
            errors.append(
                FieldError((field,), f"«{recipe.name}» в архиве — сначала верните его")
            )
    if errors:
        raise CatalogError(errors)
    return CycleSlot(slot=slot, lunch_id=lunch_id, dinner_id=dinner_id)


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
    if ingredient.kcal_100g < 0:
        errors.append(
            FieldError(("kcal_100g",), "Ккал на 100 г не может быть меньше нуля")
        )
    if not 0 <= ingredient.protein_100g <= 100:
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
        kcal_100g=ingredient.kcal_100g,
        protein_100g=ingredient.protein_100g,
        source=_text(ingredient.source),
        grams_per_unit=grams_per_unit,
        store_piece=ingredient.store_piece,
    )
