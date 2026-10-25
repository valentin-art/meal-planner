"""Доменные структуры данных. Поля повторяют meal-plan.jsx 1:1."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Literal


class MealKind(StrEnum):
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"


ShoppingTiming = Literal["same", "before"]


@dataclass(frozen=True, slots=True)
class Ingredient:
    name: str
    qty: float
    unit: str  # всегда строка, в т.ч. "шт" — часть ключа агрегации name|unit|cat (JS aggregate)
    category: str


@dataclass(frozen=True, slots=True)
class RecipeStorage:
    keep: str
    pan: str
    micro: str
    after: str | None = None


@dataclass(frozen=True, slots=True)
class Recipe:
    id: str
    name: str
    kind: MealKind
    kcal: int
    protein: int
    ingredients: tuple[Ingredient, ...]
    steps: tuple[str, ...]
    storage: RecipeStorage | None = None  # у завтраков нет
    is_fish: bool = False  # заменяет FISH_LUNCH_IDS/FISH_DINNER_IDS


@dataclass(frozen=True, slots=True)
class Settings:
    meal_anchor_date: date
    cook_count: int
    cook_weekday1: int  # 1..7, пн=1
    cook_weekday2: int | None  # при cook_count=1 игнорируется (JS cookWeekdays)
    shopping_timing: ShoppingTiming


@dataclass(frozen=True, slots=True)
class DayMeals:
    date: date
    cycle_index: int  # 1..5, meals.k после сортировки по fishPriority (JS mealsForDate)
    breakfast: Recipe
    lunch: Recipe
    dinner: Recipe
    kcal_total: int
    protein_total: int
    is_day_zero: bool  # anchor - 1: готовка, меню не показывается (JS dayZero)
    is_cook_day: bool  # is_day_zero или день недели из cookWeekdays (JS isCook)


@dataclass(frozen=True, slots=True)
class ShoppingItem:
    name: str
    qty: float
    unit: str
    category: str


@dataclass(frozen=True, slots=True)
class ShoppingGroup:
    category: str
    items: tuple[ShoppingItem, ...]


@dataclass(frozen=True, slots=True)
class CookItem:
    lunch: Recipe
    dinner: Recipe  # ужин той же позиции ротации, что и обед (JS cookItems)
    portions: int


@dataclass(frozen=True, slots=True)
class ShoppingTrip:
    shopping_date: date
    cook_date: date
    window_start: date  # cook_date + 1
    window_end: date  # следующая готовка, включительно (JS nextCookDate)
    cook_items: tuple[CookItem, ...]
    shopping_groups: tuple[ShoppingGroup, ...]


@dataclass(frozen=True, slots=True)
class AppState:
    settings: Settings
    schedule: tuple[DayMeals, ...]
    next_shopping_trip: ShoppingTrip
    recipes: Mapping[str, Recipe]
    version: int
