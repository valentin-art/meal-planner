"""Действия над каталогом (5.4): правила правки рецептов, слотов и справочника.

С 8.4b правка рецепта может менять и состав позиций цикла (plan_for):
новый обед/ужин раскладывается сам, архивный убирается — с цикла first.
С 8.6a рецепт от Assist — черновик: в позиции он встаёт только после
confirm_draft. С 8.6b он может принести новые ингредиенты, а количества
переводятся в единицы справочника (core/units.py).

Чистые функции: каталог и запрос → что записать. Ошибки собираются все
сразу (форма рецепта показывает их у полей); loc — путь к полю в теле
запроса. Ккал и белок не принимаются — их считает build_catalog (5.1).
"""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from decimal import Decimal
from types import MappingProxyType

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
from meal_planner.core.units import rounded, to_ingredient_unit, unconvertible_line

UNITS = ("г", "мл", "шт", "ч.л.", "ст.л.", "зубчик")
WEIGHT_UNITS = ("г", "мл")  # граммы в единице не нужны: 1:1
# Единицы новых ингредиентов от Assist — правило 6.7; шт и зубчик — только
# у существующих (яйца, чеснок).
ASSIST_UNITS = ("г", "мл", "ч.л.", "ст.л.")
ASSIST_SOURCE = "Assist: "
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
class ProposedLine:
    """Строка рецепта от Assist (8.6a): ингредиент — названием справочника.

    Единица — любая из units.LINE_UNITS: в единицу ингредиента переводит
    core (8.6b).
    """

    name: str
    qty: float
    unit: str


@dataclass(frozen=True, slots=True)
class NewIngredient:
    """Ингредиент, которого нет в справочнике (8.6b): ккал — оценка модели."""

    name: str
    unit: str  # одна из ASSIST_UNITS
    category: str
    kcal_100g: float
    protein_100g: float
    grams_per_unit: float | None = None  # вес ложки, г — для ч.л./ст.л.
    store_piece: float | None = None  # штука в магазине, в unit
    source: str | None = None


@dataclass(frozen=True, slots=True)
class RecipeProposal:
    name: str
    kind: MealKind
    ingredients: tuple[ProposedLine, ...]
    steps: tuple[str, ...]
    storage: RecipeStorage | None
    is_fish: bool = False
    new_ingredients: tuple[NewIngredient, ...] = ()


@dataclass(frozen=True, slots=True)
class Proposed:
    """Что записать по рецепту от Assist: черновик и новые ингредиенты."""

    recipe: RecipeSpec
    ingredients: tuple[CatalogIngredient, ...]


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
    # Архивные рецепты тоже в каталоге — их номера не переиспользуются.
    # Номер отброшенного черновика может достаться следующему: ссылок на
    # черновик нет (8.6a).
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


def _not_draft(spec: RecipeSpec) -> RecipeSpec:
    if spec.status == "draft":
        raise CatalogError(
            [
                FieldError(
                    ("id",), f"«{spec.name}» — черновик: подтвердите или отбросьте его"
                )
            ]
        )
    return spec


def _draft(catalog: Catalog, recipe_id: str) -> RecipeSpec:
    spec = _existing(catalog, recipe_id)
    if spec.status != "draft":
        raise CatalogError([FieldError(("id",), f"«{spec.name}» — не черновик")])
    return spec


def archive_recipe(catalog: Catalog, recipe_id: str) -> RecipeSpec:
    """В архив: из пула пропадает, но не удаляется (5.1). Повтор — не ошибка.

    Из позиций цикла рецепт убирает plan_for (8.4b).
    """
    spec = _not_draft(_existing(catalog, recipe_id))
    if recipe_id in ROTATION_BREAKFASTS:
        raise CatalogError(
            [FieldError(("id",), f"«{spec.name}» — завтрак в ротации, в архив нельзя")]
        )
    return replace(spec, status="archived")


def restore_recipe(catalog: Catalog, recipe_id: str) -> RecipeSpec:
    # Черновик — только через confirm_draft, не в обход подтверждения.
    return replace(_not_draft(_existing(catalog, recipe_id)), status="active")


def from_assist(ingredient: CatalogIngredient) -> bool:
    """Добавлен Assist (8.6b): ккал на 100 г — оценка модели, стоит проверить.

    Источник поправили в редакторе — метки больше нет.
    """
    return (ingredient.source or "").startswith(ASSIST_SOURCE)


def _key(name: str) -> str:
    return name.strip().casefold()


def _with_ingredient(catalog: Catalog, ingredient: CatalogIngredient) -> Catalog:
    return replace(
        catalog,
        ingredients=MappingProxyType(
            {**catalog.ingredients, ingredient.name: ingredient}
        ),
    )


def _new_ingredients(
    catalog: Catalog, proposal: RecipeProposal, errors: list[FieldError]
) -> tuple[Catalog, list[CatalogIngredient], set[str]]:
    """Справочник с новыми ингредиентами и названия непринятых.

    Уже известные берутся из справочника как есть.
    """
    known = {_key(name) for name in catalog.ingredients}
    added: list[CatalogIngredient] = []
    seen: set[str] = set()
    for n, new in enumerate(proposal.new_ingredients):
        key = _key(new.name)
        if key in known:
            continue  # модель не знала справочник — берём существующий
        loc = ("new_ingredients", n)
        if key in seen:
            errors.append(FieldError((*loc, "name"), f"«{new.name}» уже описан"))
            continue
        seen.add(key)
        if new.unit not in ASSIST_UNITS:
            errors.append(
                FieldError(
                    (*loc, "unit"),
                    f"Единица нового ингредиента — одна из: {', '.join(ASSIST_UNITS)}",
                )
            )
            continue
        try:
            ingredient = save_ingredient(
                catalog,
                IngredientInput(
                    id=None,
                    name=new.name,
                    unit=new.unit,
                    category=new.category,
                    kcal_100g=new.kcal_100g,
                    protein_100g=new.protein_100g,
                    source=ASSIST_SOURCE + (_text(new.source) or "оценка модели"),
                    grams_per_unit=new.grams_per_unit,
                    store_piece=new.store_piece,
                ),
            )
        except CatalogError as e:
            errors.extend(FieldError((*loc, *err.loc), err.message) for err in e.errors)
            continue
        catalog = _with_ingredient(catalog, ingredient)
        added.append(ingredient)
    return catalog, added, seen - {_key(i.name) for i in added}


def propose_recipe(catalog: Catalog, proposal: RecipeProposal) -> Proposed:
    """Новый черновик от Assist (8.6a): проверки — как у save_recipe.

    Существующие рецепты так не меняются. Ингредиенты — названиями без учёта
    регистра: из справочника или из new_ingredients (8.6b). Количество
    переводится в единицу ингредиента и округляется (core/units.py).
    """
    if proposal.kind == MealKind.BREAKFAST:
        raise CatalogError(
            [FieldError(("kind",), "Assist предлагает только обеды и ужины")]
        )
    errors: list[FieldError] = []
    catalog, added, rejected = _new_ingredients(catalog, proposal, errors)
    by_name = {_key(name): i for name, i in catalog.ingredients.items()}
    line_errors = False
    lines = []
    for n, line in enumerate(proposal.ingredients):
        ingredient = by_name.get(_key(line.name))
        if ingredient is None:
            line_errors = True
            if _key(line.name) in rejected:
                continue  # ошибка уже у его описания в new_ingredients
            errors.append(
                FieldError(
                    ("ingredients", n, "name"),
                    f"«{line.name}» нет в справочнике — опишите его в new_ingredients",
                )
            )
            continue
        qty = to_ingredient_unit(ingredient, Decimal(str(line.qty)), line.unit)
        if qty is None:
            line_errors = True
            errors.append(
                FieldError(
                    ("ingredients", n, "unit"),
                    unconvertible_line(ingredient.name, line.unit, ingredient.unit),
                )
            )
            continue
        lines.append(
            RecipeLineInput(ingredient.id, float(rounded(ingredient.unit, qty)))
        )
    try:
        spec = save_recipe(
            catalog,
            RecipeInput(
                id=None,
                name=proposal.name,
                kind=proposal.kind,
                ingredients=tuple(lines),
                steps=proposal.steps,
                storage=proposal.storage,
                is_fish=proposal.is_fish,
            ),
        )
    except CatalogError as e:
        # Номера строк save_recipe — среди принятых: при своих ошибках строк
        # номер мог бы съехать, их ошибки строк не показываем.
        errors.extend(
            err for err in e.errors if not (line_errors and err.loc[0] == "ingredients")
        )
        raise CatalogError(errors) from e
    if errors:
        raise CatalogError(errors)
    return Proposed(replace(spec, status="draft"), tuple(added))


def confirm_draft(catalog: Catalog, recipe_id: str) -> RecipeSpec:
    """Черновик — в пул; в позицию его ставит plan_for, как новый рецепт."""
    return replace(_draft(catalog, recipe_id), status="active")


def discard_draft(catalog: Catalog, recipe_id: str) -> str:
    """Черновик удаляется (не в архив): в плане его не было, ссылок нет."""
    return _draft(catalog, recipe_id).id


def plan_for(catalog: Catalog, spec: RecipeSpec, first: int) -> RecipeChange:
    """Состав позиций после сохранения spec (8.4b) — с цикла first.

    Новый, вернувшийся из архива или подтверждённый черновик (8.6a) обед/ужин
    встаёт в позицию сам; черновик — никуда; ушедший в архив убирается из
    позиций, если он там не единственный своего типа. Рецепт, ставший
    рыбным, не должен дать лишнюю рыбную позицию.
    """
    versions = catalog.slot_versions
    if spec.kind == MealKind.BREAKFAST or spec.status == "draft":
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
    elif existing is None or existing.status != "active":
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
            elif recipe.status == "draft":
                errors.append(
                    FieldError(
                        (field, n), f"«{recipe.name}» — черновик, сначала подтвердите"
                    )
                )
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
