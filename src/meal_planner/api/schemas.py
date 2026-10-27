"""DTO для JSON-ответов API: Pydantic-модели поверх доменных dataclass'ов.

Только перекладка полей. Дни и cook items ссылаются на рецепты по id —
справочник — отдельно, GET /api/recipes (клиент берёт его один раз).
Исключение — SummaryOut для HA: там названия и готовые строки, как в UI.
"""

from collections.abc import Mapping
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from meal_planner.core.catalog_actions import (
    IngredientInput,
    RecipeInput,
    RecipeLineInput,
    used_in,
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
from meal_planner.core.shopping import format_qty


class SettingsOut(BaseModel):
    meal_anchor_date: date
    cook_count: int
    cook_weekday1: int
    cook_weekday2: int | None
    shopping_timing: ShoppingTiming
    skipped_cook_date: date | None


class IngredientOut(BaseModel):
    name: str
    qty: float
    qty_display: str
    unit: str
    category: str


class RecipeStorageOut(BaseModel):
    keep: str
    pan: str
    micro: str
    after: str | None


class RecipeOut(BaseModel):
    id: str
    name: str
    kind: str
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


class RecipeLineIn(BaseModel):
    ingredient_id: int
    qty: float = Field(gt=0)  # в единице ингредиента


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


class RecipeIdIn(BaseModel):
    id: str


class SetCycleSlotIn(BaseModel):
    slot: int = Field(ge=1, le=5)
    lunch_id: str
    dinner_id: str


class SaveIngredientIn(BaseModel):
    id: int | None = None  # None — новый ингредиент
    name: str
    unit: str  # г, мл, шт, ст.л., зубчик — проверяет core
    category: str  # из категорий списка покупок — проверяет core
    kcal_100g: float
    protein_100g: float
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


class SummaryDaysOut(BaseModel):
    today: SummaryDayOut
    tomorrow: SummaryDayOut


class SummaryTripOut(BaseModel):
    date: date
    cook_date: date
    window_start: date
    window_end: date
    cook: list[str]  # «обед + ужин — xN», как «Что готовить» в UI
    items: list[str]  # «название — 540 г (≈ 2 шт)», как «Что купить» в UI


class SkippedCookOut(BaseModel):
    cook_date: date
    pause_start: date
    pause_end: date  # он же следующая готовка
    can_undo: bool


class SummaryOut(BaseModel):
    """Компактный ответ для REST-сенсоров и Assist в HA (7.5)."""

    today: date
    version: int
    days: SummaryDaysOut
    next_shopping: SummaryTripOut
    skipped_cook: SkippedCookOut | None


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


class RecipeActionOut(BaseModel):
    state: AppStateOut
    recipe: RecipeOut


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
    )


def to_ingredient_out(i: Ingredient) -> IngredientOut:
    return IngredientOut(
        name=i.name,
        qty=i.qty,
        qty_display=format_qty(i.qty),
        unit=i.unit,
        category=i.category,
    )


def to_recipe_out(r: Recipe) -> RecipeOut:
    storage = r.storage
    return RecipeOut(
        id=r.id,
        name=r.name,
        kind=r.kind.value,
        kcal=r.kcal,
        protein=r.protein,
        ingredients=[to_ingredient_out(i) for i in r.ingredients],
        steps=list(r.steps),
        storage=None
        if storage is None
        else RecipeStorageOut(
            keep=storage.keep, pan=storage.pan, micro=storage.micro, after=storage.after
        ),
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
    )


def to_recipes_out(recipes: Mapping[str, Recipe]) -> dict[str, RecipeOut]:
    return {rid: to_recipe_out(r) for rid, r in recipes.items()}


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
    return CookItemOut(lunch_id=c.lunch.id, dinner_id=c.dinner.id, portions=c.portions)


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


def to_app_state_out(s: AppState) -> AppStateOut:
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
    )


def shopping_line(i: ShoppingItem) -> str:
    line = f"{i.name} — {format_qty(i.qty)} {i.unit}"
    return line if i.pieces is None else f"{line} (≈ {i.pieces} шт)"


def cook_line(c: CookItem) -> str:
    return f"{c.lunch.name} + {c.dinner.name} — x{c.portions}"


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
    )


def to_summary_out(s: AppState) -> SummaryOut:
    """s — recompute на сегодня и завтра: schedule[0] и schedule[1]."""
    today, tomorrow = s.schedule
    t = s.next_shopping_trip
    return SummaryOut(
        today=s.today,
        version=s.version,
        days=SummaryDaysOut(
            today=to_summary_day_out(today), tomorrow=to_summary_day_out(tomorrow)
        ),
        next_shopping=SummaryTripOut(
            date=t.shopping_date,
            cook_date=t.cook_date,
            window_start=t.window_start,
            window_end=t.window_end,
            cook=[cook_line(c) for c in t.cook_items],
            items=[shopping_line(i) for g in t.shopping_groups for i in g.items],
        ),
        skipped_cook=to_skipped_cook_out(s.skipped_cook),
    )
