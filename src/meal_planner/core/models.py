"""Доменные структуры данных. Поля повторяют meal-plan.jsx 1:1."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
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


def default_settings(today: date) -> Settings:
    # JS DEFAULT_SETTINGS: якорь — завтра, готовка Ср/Сб, закупка в день готовки.
    # Якорь — «завтра» от первого обращения: API сохраняет дефолты при первом
    # GET /api/state (6.4), иначе цикл сдвигался бы каждый день, как в JS.
    return Settings(
        meal_anchor_date=today + timedelta(days=1),
        cook_count=2,
        cook_weekday1=3,
        cook_weekday2=6,
        shopping_timing="same",
    )


@dataclass(frozen=True, slots=True)
class DayMeals:
    date: date
    # 1..5, meals.k после сортировки по fishPriority (JS mealsForDate).
    # До якоря (до старта и день 0) блюд нет: cycle_index и блюда — None (6.5).
    cycle_index: int | None
    breakfast: Recipe | None
    lunch: Recipe | None
    dinner: Recipe | None
    kcal_total: int
    protein_total: int
    is_day_zero: bool  # anchor - 1: первая готовка, блюд ещё нет
    is_cook_day: bool  # is_day_zero или день недели готовки после старта
    is_before_start: bool  # раньше дня 0: цикл ещё не начался


@dataclass(frozen=True, slots=True)
class ShoppingItem:
    name: str
    qty: float
    unit: str
    category: str
    pieces: int | None  # «≈ N шт» для продаваемого поштучно (6.7), иначе None


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
class CyclePosition:
    """Что может быть сегодня и какой якорь к этому приводит (6.6)."""

    dish: int  # 0 — сегодня день 0 (готовка, цикл с завтра), 1..5 — cycle_index
    anchor: date
    lunch: Recipe | None  # блюдо позиции; у dish=0 — None
    dinner: Recipe | None


@dataclass(frozen=True, slots=True)
class TodayPosition:
    current: int | None  # как в options; None — цикл ещё не начался
    options: tuple[CyclePosition, ...]  # по dish, всегда с 0


@dataclass(frozen=True, slots=True)
class AppState:
    today: date  # от него считается поездка в магазин; UI берёт «сегодня» отсюда
    settings: Settings
    schedule: tuple[DayMeals, ...]
    next_shopping_trip: ShoppingTrip
    recipes: Mapping[str, Recipe]
    version: int
    today_position: TodayPosition
