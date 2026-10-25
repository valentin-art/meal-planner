"""DTO для JSON-ответов API: Pydantic-модели поверх доменных dataclass'ов.

Только перекладка полей. Дни и cook items ссылаются на рецепты по id —
полный справочник отдаётся один раз в AppStateOut.recipes.
"""

from collections.abc import Mapping
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from meal_planner.core.models import (
    AppState,
    CookItem,
    DayMeals,
    Ingredient,
    Recipe,
    Settings,
    ShoppingGroup,
    ShoppingItem,
    ShoppingTiming,
    ShoppingTrip,
)
from meal_planner.core.shopping import format_qty


class SettingsOut(BaseModel):
    meal_anchor_date: date
    cook_count: int
    cook_weekday1: int
    cook_weekday2: int | None
    shopping_timing: ShoppingTiming


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


class ShoppingItemOut(BaseModel):
    name: str
    qty: float
    qty_display: str
    unit: str
    category: str


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


class AppStateOut(BaseModel):
    today: date
    settings: SettingsOut
    schedule: list[DayOut]
    next_shopping_trip: ShoppingTripOut
    recipes: dict[str, RecipeOut]
    version: int


def to_settings_out(s: Settings) -> SettingsOut:
    return SettingsOut(
        meal_anchor_date=s.meal_anchor_date,
        cook_count=s.cook_count,
        cook_weekday1=s.cook_weekday1,
        cook_weekday2=s.cook_weekday2,
        shopping_timing=s.shopping_timing,
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
    )


def to_shopping_item_out(i: ShoppingItem) -> ShoppingItemOut:
    return ShoppingItemOut(
        name=i.name,
        qty=i.qty,
        qty_display=format_qty(i.qty),
        unit=i.unit,
        category=i.category,
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


def to_app_state_out(s: AppState) -> AppStateOut:
    return AppStateOut(
        today=s.today,
        settings=to_settings_out(s.settings),
        schedule=[to_day_out(d) for d in s.schedule],
        next_shopping_trip=to_shopping_trip_out(s.next_shopping_trip),
        recipes=to_recipes_out(s.recipes),
        version=s.version,
    )
