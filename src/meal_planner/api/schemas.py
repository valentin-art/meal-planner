"""DTO для JSON-ответов API: Pydantic-модели поверх доменных dataclass'ов.

Только перекладка полей. Дни и cook items ссылаются на рецепты по id —
справочник — отдельно, GET /api/recipes (клиент берёт его один раз).
Исключения — SummaryOut и CookbookOut для HA: там названия и готовые
строки, как в UI.
"""

import datetime as dt
from collections.abc import Mapping
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from meal_planner.core.catalog_actions import (
    UNITS,
    IngredientInput,
    NewIngredient,
    ProposedLine,
    RecipeInput,
    RecipeLineInput,
    RecipeProposal,
    from_assist,
    used_in,
)
from meal_planner.core.cookbook import UpcomingCook
from meal_planner.core.cooking import (
    MAX_PORTIONS,
    MIN_PORTIONS,
    Cooking,
    CounterUpdate,
)
from meal_planner.core.models import (
    AppState,
    Catalog,
    CatalogIngredient,
    CookItem,
    DayMeals,
    Ingredient,
    MealKind,
    PlanUse,
    Recipe,
    RecipeStatus,
    RecipeStorage,
    Settings,
    ShoppingGroup,
    ShoppingItem,
    ShoppingTiming,
    ShoppingTrip,
    SkippedCook,
    TodayPosition,
)
from meal_planner.core.shopping import CATEGORY_ORDER, format_qty
from meal_planner.core.stock import StockedTrip, StockIgnored, StockLine
from meal_planner.core.validation import (
    KCAL_TARGET_MAX,
    KCAL_TARGET_MIN,
    PROTEIN_TARGET_MAX,
    PROTEIN_TARGET_MIN,
)


class SettingsOut(BaseModel):
    meal_anchor_date: date
    cook_count: int
    cook_weekday1: int
    cook_weekday2: int | None
    shopping_timing: ShoppingTiming
    skipped_cook_date: date | None
    daily_kcal_target: int | None  # null — цели нет (8.1)
    daily_protein_target: int | None


class IngredientOut(BaseModel):
    ingredient_id: int  # id в справочнике — редактор рецепта ссылается по нему
    name: str
    qty: float
    qty_display: str
    unit: str
    category: str
    from_assist: bool  # добавлен Assist (8.6b): ккал на 100 г — оценка модели


class RecipeStorageOut(BaseModel):
    keep: str
    pan: str
    micro: str
    after: str | None


class RecipeOut(BaseModel):
    id: str
    name: str
    kind: MealKind
    kcal: int
    protein: int
    ingredients: list[IngredientOut]
    steps: list[str]
    storage: RecipeStorageOut | None
    is_fish: bool
    status: RecipeStatus  # архивный — не в пуле, но не удалён (5.1)


class CatalogIngredientOut(BaseModel):
    """Ингредиент справочника (5.4) — GET /api/ingredients, save-ingredient."""

    id: int
    name: str
    unit: str
    category: str
    kcal_100g: float
    protein_100g: float
    source: str | None
    grams_per_unit: float | None  # для единиц не г/мл
    store_piece: float | None  # штука в магазине, в unit — «≈ N шт»
    used_in: list[str]  # id рецептов: единицу при них не поменять
    from_assist: bool  # добавлен Assist (8.6b): значения — оценка модели


class IngredientsOut(BaseModel):
    """GET /api/ingredients: справочник и варианты для формы (5.6).

    Единицы и категории — правило core/, форма берёт их отсюда.
    """

    ingredients: list[CatalogIngredientOut]
    units: list[str]
    categories: list[str]  # в порядке списка покупок


class CycleSlotOut(BaseModel):
    slot: int  # 1..5 — позиция ротации
    # Варианты по кругу, цикл за циклом (8.4b); последняя версия состава.
    lunch_ids: list[str]
    dinner_ids: list[str]
    # С какого дня действует последняя правка позиции; null — уже действует.
    changes_from: date | None


class DayOut(BaseModel):
    date: date
    # До якоря (до старта и день 0) блюд нет — null.
    cycle_index: int | None
    breakfast_id: str | None
    lunch_id: str | None
    dinner_id: str | None
    kcal_total: int
    protein_total: int
    is_day_zero: bool
    is_cook_day: bool
    is_before_start: bool
    is_skipped: bool  # пауза после пропущенной готовки — блюд нет (7.7)
    # Итог минус цель (8.1): null — цели нет или в этот день нет блюд.
    kcal_delta: int | None
    protein_delta: int | None
    # Множитель порции под цель (8.5); 1 — как в рецепте. Граммы и ккал
    # порции — в AppStateOut.scaled_recipes.
    lunch_scale: float
    dinner_scale: float


class ShoppingItemOut(BaseModel):
    name: str
    qty: float
    qty_display: str
    unit: str
    category: str
    pieces: int | None  # «≈ N шт» для продаваемого поштучно, иначе null


class ShoppingGroupOut(BaseModel):
    category: str
    items: list[ShoppingItemOut]


class CookItemOut(BaseModel):
    lunch_id: str
    dinner_id: str
    portions: int
    lunch_scale: float  # множитель порции (8.5)
    dinner_scale: float


class ShoppingTripOut(BaseModel):
    shopping_date: date
    cook_date: date
    window_start: date
    window_end: date
    window_days: int  # JS windowLen — «N дней» в UI
    cook_items: list[CookItemOut]
    shopping_groups: list[ShoppingGroupOut]


class SetCookScheduleIn(BaseModel):
    # ge/le дублируют core/validation намеренно: так пределы видны в OpenAPI.
    # Обязательность второго дня проверяется по итоговым настройкам в core.
    cook_count: Literal[1, 2]
    cook_weekday1: int = Field(ge=1, le=7)
    cook_weekday2: int | None = Field(default=None, ge=1, le=7)  # None — оставить


class SetShoppingTimingIn(BaseModel):
    shopping_timing: ShoppingTiming


class SetAnchorDateIn(BaseModel):
    meal_anchor_date: date  # "" или кривая дата → 422, ничего не сохраняется


class SetNutritionTargetIn(BaseModel):
    # Обе цели целиком, null — цели нет. ge/le дублируют core/validation.
    daily_kcal_target: int | None = Field(ge=KCAL_TARGET_MIN, le=KCAL_TARGET_MAX)
    daily_protein_target: int | None = Field(
        ge=PROTEIN_TARGET_MIN, le=PROTEIN_TARGET_MAX
    )


class RecipeLineIn(BaseModel):
    ingredient_id: int
    # В единице ингредиента. Без gt=0: «больше нуля» проверяет core — по-русски
    # и у поля, как остальные ошибки формы.
    qty: float


class RecipeStorageIn(BaseModel):
    keep: str
    pan: str
    micro: str
    after: str | None = None


class SaveRecipeIn(BaseModel):
    # Ккал и белка нет — их считает сервер из ингредиентов (5.1).
    id: str | None = None  # None — новый рецепт, id выдаёт сервер
    name: str
    kind: MealKind  # при правке не меняется
    is_fish: bool = False
    ingredients: list[RecipeLineIn]
    steps: list[str]
    storage: RecipeStorageIn | None = None  # у завтрака нет

    def to_input(self) -> RecipeInput:
        storage = self.storage
        return RecipeInput(
            id=self.id,
            name=self.name,
            kind=self.kind,
            ingredients=tuple(
                RecipeLineInput(i.ingredient_id, i.qty) for i in self.ingredients
            ),
            steps=tuple(self.steps),
            storage=None
            if storage is None
            else RecipeStorage(storage.keep, storage.pan, storage.micro, storage.after),
            is_fish=self.is_fish,
        )


class ProposedLineIn(BaseModel):
    name: str  # из справочника или new_ingredients, регистр не важен
    qty: float  # «больше нуля» проверяет core
    unit: str  # г, кг, мл, л, стакан, ч.л., ст.л., шт, зубчик — переводит core


class NewIngredientIn(BaseModel):
    """Ингредиента нет в справочнике — описание от Assist (8.6b)."""

    name: str
    unit: str  # г — твёрдое, мл — жидкое, ч.л./ст.л. — специи, масла, соусы
    category: str  # из категорий списка покупок — проверяет core
    kcal_100g: float
    protein_100g: float
    grams_per_unit: float | None = None  # вес ложки, г — для ч.л./ст.л.
    store_piece: float | None = None  # штука в магазине, в unit
    source: str | None = None  # откуда значения; core допишет «Assist: »

    def to_new(self) -> NewIngredient:
        return NewIngredient(**self.model_dump())


class ProposeRecipeIn(BaseModel):
    """Рецепт от Assist (8.6a): только новый черновик, обед или ужин."""

    name: str
    kind: MealKind  # завтрак отклоняет core — по-русски
    is_fish: bool = False
    ingredients: list[ProposedLineIn]
    steps: list[str]
    storage: RecipeStorageIn | None = None
    new_ingredients: list[NewIngredientIn] = []

    def to_proposal(self) -> RecipeProposal:
        storage = self.storage
        return RecipeProposal(
            name=self.name,
            kind=self.kind,
            ingredients=tuple(
                ProposedLine(i.name, i.qty, i.unit) for i in self.ingredients
            ),
            steps=tuple(self.steps),
            storage=None
            if storage is None
            else RecipeStorage(storage.keep, storage.pan, storage.micro, storage.after),
            is_fish=self.is_fish,
            new_ingredients=tuple(i.to_new() for i in self.new_ingredients),
        )


class UpdateDraftIn(ProposeRecipeIn):
    """Правка своего черновика (8.6c): тело то же, плюс id черновика."""

    id: str


class RecipePreviewLineIn(BaseModel):
    ingredient_id: int
    qty: float  # без gt=0: черновик бывает недописан, такие строки не считаются


class RecipePreviewIn(BaseModel):
    ingredients: list[RecipePreviewLineIn]

    def to_lines(self) -> list[RecipeLineInput]:
        return [RecipeLineInput(i.ingredient_id, i.qty) for i in self.ingredients]


class NutritionOut(BaseModel):
    kcal: int
    protein: int


class SetPortionScalingIn(BaseModel):
    enabled: bool  # подгонять порции обедов и ужинов под цель по ккал (8.5)


class RecipeIdIn(BaseModel):
    id: str


class SetCycleSlotIn(BaseModel):
    slot: int = Field(ge=1, le=5)
    # Состав позиции целиком (8.4b); пустой список отклоняет core — по-русски.
    lunch_ids: list[str]
    dinner_ids: list[str]


class SaveIngredientIn(BaseModel):
    id: int | None = None  # None — новый ингредиент
    name: str
    unit: str  # г, мл, шт, ст.л., зубчик — проверяет core
    category: str  # из категорий списка покупок — проверяет core
    # Пустое поле формы — null: «укажите …» по-русски проверяет core.
    kcal_100g: float | None
    protein_100g: float | None
    source: str | None = None
    grams_per_unit: float | None = None  # обязательно для единиц не г/мл
    store_piece: float | None = None

    def to_input(self) -> IngredientInput:
        return IngredientInput(**self.model_dump())


class SetCyclePositionIn(BaseModel):
    # 0 — сегодня день 0 (готовка, цикл с завтра); 1..5 — «сегодня блюдо №N».
    # Недостижимое сегодня блюдо → 422 (варианты — в today_position.options).
    dish: int = Field(ge=0, le=5)


class CyclePositionOut(BaseModel):
    dish: int
    anchor: date
    lunch_id: str | None
    dinner_id: str | None


class TodayPositionOut(BaseModel):
    current: int | None  # None — цикл ещё не начался
    options: list[CyclePositionOut]


class SummaryDayOut(BaseModel):
    date: date
    # До старта и в день 0 блюд нет — null, флаги объясняют почему.
    breakfast: str | None
    lunch: str | None
    dinner: str | None
    kcal: int
    protein: int
    is_cook_day: bool
    is_day_zero: bool
    is_before_start: bool
    is_skipped: bool
    kcal_delta: int | None  # итог минус цель (8.1), null — нет цели или блюд
    protein_delta: int | None
    # «≈ 1790 ккал (−210) · 95 г белка (−5)», как итог дня в UI; без блюд — null.
    nutrition: str | None


class SummaryDaysOut(BaseModel):
    today: SummaryDayOut
    tomorrow: SummaryDayOut


class SummaryTripOut(BaseModel):
    # Поле date (его читает пакет HA) перекрывает тип date для полей ниже.
    date: dt.date
    cook_date: dt.date
    window_start: dt.date
    window_end: dt.date
    cook: list[str]  # «обед + ужин — xN», как «Что готовить» в UI
    items: list[str]  # «название — 540 г (≈ 2 шт)», как «Что купить» в UI


class StockLineIn(BaseModel):
    ingredient_id: int  # id справочника — переименование не ломает связку
    qty: float
    unit: str  # единица счётчика: г, мл, шт, кг, л…; переводит core
    # Сущность счётчика в HA — по ней списание (8.3b) отдаёт новые значения.
    entity_id: str | None = None


class ShoppingRemainingIn(BaseModel):
    """Остатки клиента (счётчики HA, 8.2) — приложение их не хранит."""

    stock: list[StockLineIn]

    def to_lines(self) -> list[StockLine]:
        return [
            StockLine(i.ingredient_id, i.qty, i.unit, i.entity_id) for i in self.stock
        ]


class StockIgnoredOut(BaseModel):
    ingredient_id: int
    name: str | None  # null — такого ингредиента нет в справочнике
    qty: float
    unit: str
    reason: str  # по-русски, можно зачитать


class ShoppingRemainingOut(SummaryTripOut):
    """Ближайшая закупка за вычетом остатков (8.2), строки — как в summary."""

    ignored: list[StockIgnoredOut]  # строки остатков, которые не учтены


class CookedIn(ShoppingRemainingIn):
    """Отметить или снять «приготовлено» (8.3b) — с остатками из счётчиков HA.

    День отметки (день готовки партии или сегодняшний завтрак) решает core.
    """

    recipe_id: str


class SetDishPortionsIn(BaseModel):
    """Порции неотмеченного блюда (8.3b) — меняют только списание, не план."""

    recipe_id: str
    # ge/le дублируют core намеренно: так пределы видны в OpenAPI.
    portions: int = Field(ge=MIN_PORTIONS, le=MAX_PORTIONS)


class DishPortionsOut(BaseModel):
    version: int
    day: date  # ключ: день готовки партии или сегодня (завтрак)
    recipe_id: str
    portions: int


class CounterValueOut(BaseModel):
    entity_id: str
    value: float  # новое значение счётчика, в его единице, не меньше 0


class CookedActionOut(BaseModel):
    version: int
    day: date  # день отметки: готовка партии или сегодня (завтрак)
    recipe_id: str
    counters: list[CounterValueOut]  # выставить в HA как есть
    ignored: list[StockIgnoredOut]  # счётчик есть, но единицу не перевести
    untracked: list[str]  # ингредиенты блюда без счётчика — не списаны


class SummaryDishOut(BaseModel):
    recipe_id: str
    name: str
    kind: MealKind
    portions: int  # сколько готовят — выбранное или плановое
    planned_portions: int  # сколько дней блюдо едят по плану
    cooked: bool


class SummaryBreakfastOut(BaseModel):
    date: date
    recipe_id: str
    name: str
    kind: MealKind  # всегда breakfast — плитки читают одно поле у всех блюд
    portions: int
    planned_portions: int
    cooked: bool


class SummaryCookingOut(BaseModel):
    """Что можно отметить приготовленным (8.3b)."""

    cook_date: date | None  # null — партии нет (до дня 0, пропуск)
    dishes: list[SummaryDishOut]  # обеды и ужины партии, по рецепту
    breakfast: SummaryBreakfastOut | None  # завтрак сегодня, null — блюд нет


class CookbookIngredientOut(BaseModel):
    name: str
    qty_display: str  # format_qty, как в карточке рецепта
    unit: str


class CookbookDishOut(BaseModel):
    recipe_id: str
    name: str
    kind: MealKind
    planned_portions: int  # сколько дней блюдо едят по плану
    scale: float  # порция под цель (8.5); 1 — без масштаба
    kcal: int  # одной порции — с масштабом
    protein: int
    ingredients: list[CookbookIngredientOut]  # на 1 порцию, с масштабом


class CookbookCookOut(BaseModel):
    # Поле date перекрывает тип date для полей ниже.
    date: dt.date  # ближайшая готовка: не раньше сегодня
    is_today: bool
    dishes: list[CookbookDishOut]  # обеды и ужины, по рецепту


class CookbookRecipeOut(BaseModel):
    id: str
    name: str
    kind: MealKind
    kcal: int
    protein: int
    is_fish: bool
    ingredients: list[CookbookIngredientOut]  # базовая порция ×1
    steps: list[str]
    storage: RecipeStorageOut | None


class CookbookOut(BaseModel):
    """Книга рецептов для вкладки «Рецепты» HA (9.1): атрибуты сенсора.

    Шаги и хранение — только в группах: плитка ближайшей готовки берёт их
    по recipe_id. Ингредиенты — без id и категории, сенсор и так большой.
    """

    today: date
    cook: CookbookCookOut
    lunch: list[CookbookRecipeOut]
    dinner: list[CookbookRecipeOut]
    breakfast: list[CookbookRecipeOut]


class SkippedCookOut(BaseModel):
    cook_date: date
    pause_start: date
    pause_end: date  # он же следующая готовка
    can_undo: bool


class PortionScalingOut(BaseModel):
    enabled: bool  # порции обедов и ужинов подгоняются под цель (8.5)
    kcal_target: int | None  # под какую цель — последняя правка
    # С какого дня действует последняя правка; null — уже действует.
    changes_from: date | None


class ScaledRecipeOut(BaseModel):
    """Обед или ужин в порции под цель (8.5): ключ — recipe_id и scale."""

    recipe_id: str
    scale: float
    kcal: int
    protein: int
    ingredients: list[IngredientOut]


class CycleOrderOut(BaseModel):
    shuffled: bool  # порядок слотов перемешан (8.4a)
    # С какого дня действует последнее изменение порядка; null — уже действует.
    changes_from: date | None


class SummaryOut(BaseModel):
    """Компактный ответ для REST-сенсоров и Assist в HA (7.5)."""

    today: date
    version: int
    kcal_target: int | None  # суточная цель (8.1), null — нет
    protein_target: int | None
    days: SummaryDaysOut
    next_shopping: SummaryTripOut
    skipped_cook: SkippedCookOut | None
    cooking: SummaryCookingOut


class HealthOut(BaseModel):
    status: Literal["ok"]


class AppStateOut(BaseModel):
    today: date
    settings: SettingsOut
    schedule: list[DayOut]
    next_shopping_trip: ShoppingTripOut
    version: int
    today_position: TodayPositionOut
    skipped_cook: SkippedCookOut | None  # пропущенная готовка и пауза (7.7)
    can_skip_next_cook: bool
    # Рецепты в плане (5.1): "bought" — продукты уже куплены, "next_trip" — в
    # ближайшей закупке; остальных нет. Правка такого рецепта меняет план.
    recipe_plan_use: dict[str, PlanUse]
    cycle_slots: list[CycleSlotOut]  # обеды и ужины каждой позиции (5.6, 8.4b)
    cycle_order: CycleOrderOut
    portion_scaling: PortionScalingOut
    scaled_recipes: list[ScaledRecipeOut]


class RecipeActionOut(BaseModel):
    state: AppStateOut
    recipe: RecipeOut


class DiscardDraftOut(BaseModel):
    state: AppStateOut
    id: str  # удалённый черновик — убрать из справочника рецептов


class IngredientActionOut(BaseModel):
    state: AppStateOut
    ingredient: CatalogIngredientOut


def to_settings_out(s: Settings) -> SettingsOut:
    return SettingsOut(
        meal_anchor_date=s.meal_anchor_date,
        cook_count=s.cook_count,
        cook_weekday1=s.cook_weekday1,
        cook_weekday2=s.cook_weekday2,
        shopping_timing=s.shopping_timing,
        skipped_cook_date=s.skipped_cook_date,
        daily_kcal_target=s.daily_kcal_target,
        daily_protein_target=s.daily_protein_target,
    )


def to_ingredient_out(catalog: Catalog, i: Ingredient) -> IngredientOut:
    ingredient = catalog.ingredients[i.name]
    return IngredientOut(
        ingredient_id=ingredient.id,
        name=i.name,
        qty=i.qty,
        qty_display=format_qty(i.qty),
        unit=i.unit,
        category=i.category,
        from_assist=from_assist(ingredient),
    )


def to_storage_out(storage: RecipeStorage | None) -> RecipeStorageOut | None:
    if storage is None:
        return None
    return RecipeStorageOut(
        keep=storage.keep, pan=storage.pan, micro=storage.micro, after=storage.after
    )


def to_recipe_out(catalog: Catalog, r: Recipe) -> RecipeOut:
    return RecipeOut(
        id=r.id,
        name=r.name,
        kind=r.kind,
        kcal=r.kcal,
        protein=r.protein,
        ingredients=[to_ingredient_out(catalog, i) for i in r.ingredients],
        steps=list(r.steps),
        storage=to_storage_out(r.storage),
        is_fish=r.is_fish,
        status=r.status,
    )


def to_catalog_ingredient_out(
    catalog: Catalog, i: CatalogIngredient
) -> CatalogIngredientOut:
    return CatalogIngredientOut(
        id=i.id,
        name=i.name,
        unit=i.unit,
        category=i.category,
        kcal_100g=i.kcal_100g,
        protein_100g=i.protein_100g,
        source=i.source,
        grams_per_unit=i.grams_per_unit,
        store_piece=i.store_piece,
        used_in=used_in(catalog, i.name),
        from_assist=from_assist(i),
    )


def to_ingredients_out(catalog: Catalog) -> IngredientsOut:
    return IngredientsOut(
        ingredients=[
            to_catalog_ingredient_out(catalog, i) for i in catalog.ingredients.values()
        ],
        units=list(UNITS),
        categories=list(CATEGORY_ORDER),
    )


def to_recipes_out(catalog: Catalog) -> dict[str, RecipeOut]:
    return {rid: to_recipe_out(catalog, r) for rid, r in catalog.recipes.items()}


def to_day_out(d: DayMeals) -> DayOut:
    return DayOut(
        date=d.date,
        cycle_index=d.cycle_index,
        breakfast_id=d.breakfast.id if d.breakfast else None,
        lunch_id=d.lunch.id if d.lunch else None,
        dinner_id=d.dinner.id if d.dinner else None,
        kcal_total=d.kcal_total,
        protein_total=d.protein_total,
        is_day_zero=d.is_day_zero,
        is_cook_day=d.is_cook_day,
        is_before_start=d.is_before_start,
        is_skipped=d.is_skipped,
        kcal_delta=d.kcal_delta,
        protein_delta=d.protein_delta,
        lunch_scale=d.lunch.scale if d.lunch else 1.0,
        dinner_scale=d.dinner.scale if d.dinner else 1.0,
    )


def to_shopping_item_out(i: ShoppingItem) -> ShoppingItemOut:
    return ShoppingItemOut(
        name=i.name,
        qty=i.qty,
        qty_display=format_qty(i.qty),
        unit=i.unit,
        category=i.category,
        pieces=i.pieces,
    )


def to_shopping_group_out(g: ShoppingGroup) -> ShoppingGroupOut:
    return ShoppingGroupOut(
        category=g.category, items=[to_shopping_item_out(i) for i in g.items]
    )


def to_cook_item_out(c: CookItem) -> CookItemOut:
    return CookItemOut(
        lunch_id=c.lunch.id,
        dinner_id=c.dinner.id,
        portions=c.portions,
        lunch_scale=c.lunch.scale,
        dinner_scale=c.dinner.scale,
    )


def to_shopping_trip_out(t: ShoppingTrip) -> ShoppingTripOut:
    return ShoppingTripOut(
        shopping_date=t.shopping_date,
        cook_date=t.cook_date,
        window_start=t.window_start,
        window_end=t.window_end,
        window_days=(t.window_end - t.window_start).days + 1,
        cook_items=[to_cook_item_out(c) for c in t.cook_items],
        shopping_groups=[to_shopping_group_out(g) for g in t.shopping_groups],
    )


def to_today_position_out(p: TodayPosition) -> TodayPositionOut:
    return TodayPositionOut(
        current=p.current,
        options=[
            CyclePositionOut(
                dish=o.dish,
                anchor=o.anchor,
                lunch_id=o.lunch.id if o.lunch else None,
                dinner_id=o.dinner.id if o.dinner else None,
            )
            for o in p.options
        ],
    )


def to_skipped_cook_out(c: SkippedCook | None) -> SkippedCookOut | None:
    if c is None:
        return None
    return SkippedCookOut(
        cook_date=c.cook_date,
        pause_start=c.pause_start,
        pause_end=c.pause_end,
        can_undo=c.can_undo,
    )


def to_app_state_out(s: AppState, catalog: Catalog) -> AppStateOut:
    return AppStateOut(
        today=s.today,
        settings=to_settings_out(s.settings),
        schedule=[to_day_out(d) for d in s.schedule],
        next_shopping_trip=to_shopping_trip_out(s.next_shopping_trip),
        version=s.version,
        today_position=to_today_position_out(s.today_position),
        skipped_cook=to_skipped_cook_out(s.skipped_cook),
        can_skip_next_cook=s.can_skip_next_cook,
        recipe_plan_use=dict(s.plan_use),
        cycle_slots=[
            CycleSlotOut(
                slot=c.slot,
                lunch_ids=list(c.lunch_ids),
                dinner_ids=list(c.dinner_ids),
                changes_from=c.changes_from,
            )
            for c in s.slots
        ],
        cycle_order=CycleOrderOut(
            shuffled=s.cycle_order.shuffled, changes_from=s.cycle_order.changes_from
        ),
        portion_scaling=PortionScalingOut(
            enabled=s.portion_scaling.enabled,
            kcal_target=s.portion_scaling.kcal_target,
            changes_from=s.portion_scaling.changes_from,
        ),
        scaled_recipes=[
            ScaledRecipeOut(
                recipe_id=r.id,
                scale=r.scale,
                kcal=r.kcal,
                protein=r.protein,
                ingredients=[to_ingredient_out(catalog, i) for i in r.ingredients],
            )
            for r in s.scaled_recipes
        ],
    )


def shopping_line(i: ShoppingItem) -> str:
    line = f"{i.name} — {format_qty(i.qty)} {i.unit}"
    return line if i.pieces is None else f"{line} (≈ {i.pieces} шт)"


def cook_line(c: CookItem) -> str:
    return f"{c.lunch.name} + {c.dinner.name} — x{c.portions}"


def signed(n: int) -> str:
    # Минус — U+2212, как во фронтенде (format.ts fmtDelta).
    return "±0" if n == 0 else f"+{n}" if n > 0 else f"−{-n}"


def nutrition_line(d: DayMeals) -> str | None:
    if d.cycle_index is None:
        return None
    kcal = f"≈ {d.kcal_total} ккал"
    protein = f"{d.protein_total} г белка"
    if d.kcal_delta is not None:
        kcal += f" ({signed(d.kcal_delta)})"
    if d.protein_delta is not None:
        protein += f" ({signed(d.protein_delta)})"
    return f"{kcal} · {protein}"


def to_summary_day_out(d: DayMeals) -> SummaryDayOut:
    return SummaryDayOut(
        date=d.date,
        breakfast=d.breakfast.name if d.breakfast else None,
        lunch=d.lunch.name if d.lunch else None,
        dinner=d.dinner.name if d.dinner else None,
        kcal=d.kcal_total,
        protein=d.protein_total,
        is_cook_day=d.is_cook_day,
        is_day_zero=d.is_day_zero,
        is_before_start=d.is_before_start,
        is_skipped=d.is_skipped,
        kcal_delta=d.kcal_delta,
        protein_delta=d.protein_delta,
        nutrition=nutrition_line(d),
    )


def to_summary_trip_out(t: ShoppingTrip) -> SummaryTripOut:
    return SummaryTripOut(
        date=t.shopping_date,
        cook_date=t.cook_date,
        window_start=t.window_start,
        window_end=t.window_end,
        cook=[cook_line(c) for c in t.cook_items],
        items=[shopping_line(i) for g in t.shopping_groups for i in g.items],
    )


def to_stock_ignored_out(i: StockIgnored) -> StockIgnoredOut:
    return StockIgnoredOut(
        ingredient_id=i.ingredient_id,
        name=i.name,
        qty=i.qty,
        unit=i.unit,
        reason=i.reason,
    )


def to_shopping_remaining_out(s: StockedTrip) -> ShoppingRemainingOut:
    return ShoppingRemainingOut(
        **to_summary_trip_out(s.trip).model_dump(),
        ignored=[to_stock_ignored_out(i) for i in s.ignored],
    )


def to_cookbook_ingredient_out(i: Ingredient) -> CookbookIngredientOut:
    return CookbookIngredientOut(
        name=i.name, qty_display=format_qty(i.qty), unit=i.unit
    )


def to_cookbook_recipe_out(r: Recipe) -> CookbookRecipeOut:
    return CookbookRecipeOut(
        id=r.id,
        name=r.name,
        kind=r.kind,
        kcal=r.kcal,
        protein=r.protein,
        is_fish=r.is_fish,
        ingredients=[to_cookbook_ingredient_out(i) for i in r.ingredients],
        steps=list(r.steps),
        storage=to_storage_out(r.storage),
    )


def to_cookbook_out(
    today: date, cook: UpcomingCook, book: Mapping[MealKind, tuple[Recipe, ...]]
) -> CookbookOut:
    def group(kind: MealKind) -> list[CookbookRecipeOut]:
        return [to_cookbook_recipe_out(r) for r in book[kind]]

    return CookbookOut(
        today=today,
        cook=CookbookCookOut(
            date=cook.cook_date,
            is_today=cook.cook_date == today,
            dishes=[
                CookbookDishOut(
                    recipe_id=d.recipe.id,
                    name=d.recipe.name,
                    kind=d.recipe.kind,
                    planned_portions=d.planned_portions,
                    scale=d.recipe.scale,
                    kcal=d.recipe.kcal,
                    protein=d.recipe.protein,
                    ingredients=[
                        to_cookbook_ingredient_out(i) for i in d.recipe.ingredients
                    ],
                )
                for d in cook.dishes
            ],
        ),
        lunch=group(MealKind.LUNCH),
        dinner=group(MealKind.DINNER),
        breakfast=group(MealKind.BREAKFAST),
    )


def to_summary_cooking_out(c: Cooking) -> SummaryCookingOut:
    b = c.breakfast
    return SummaryCookingOut(
        cook_date=c.batch and c.batch.cook_date,
        dishes=[]
        if c.batch is None
        else [
            SummaryDishOut(
                recipe_id=d.recipe.id,
                name=d.recipe.name,
                kind=d.recipe.kind,
                portions=d.portions,
                planned_portions=d.planned_portions,
                cooked=d.cooked,
            )
            for d in c.batch.dishes
        ],
        breakfast=None
        if b is None
        else SummaryBreakfastOut(
            date=b.date,
            recipe_id=b.recipe.id,
            name=b.recipe.name,
            kind=b.recipe.kind,
            portions=b.portions,
            planned_portions=b.planned_portions,
            cooked=b.cooked,
        ),
    )


def to_cooked_action_out(
    version: int, day: date, recipe_id: str, u: CounterUpdate
) -> CookedActionOut:
    return CookedActionOut(
        version=version,
        day=day,
        recipe_id=recipe_id,
        counters=[CounterValueOut(entity_id=e, value=v) for e, v in u.values],
        ignored=[to_stock_ignored_out(i) for i in u.ignored],
        untracked=list(u.untracked),
    )


def to_summary_out(s: AppState, cooking: Cooking) -> SummaryOut:
    """s — recompute на сегодня и завтра: schedule[0] и schedule[1]."""
    today, tomorrow = s.schedule
    return SummaryOut(
        today=s.today,
        version=s.version,
        kcal_target=s.settings.daily_kcal_target,
        protein_target=s.settings.daily_protein_target,
        days=SummaryDaysOut(
            today=to_summary_day_out(today), tomorrow=to_summary_day_out(tomorrow)
        ),
        next_shopping=to_summary_trip_out(s.next_shopping_trip),
        skipped_cook=to_skipped_cook_out(s.skipped_cook),
        cooking=to_summary_cooking_out(cooking),
    )
