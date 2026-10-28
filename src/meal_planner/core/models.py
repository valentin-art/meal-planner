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
# Архивный рецепт не удаляется: на него могли ссылаться слоты (5.1). В 8.6
# добавится "draft".
RecipeStatus = Literal["active", "archived"]
# Рецепт в плане (5.1): "bought" — съедается до окна ближайшей закупки,
# продукты уже куплены; "next_trip" — в окне ближайшей закупки.
PlanUse = Literal["bought", "next_trip"]


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
class RecipeSpec:
    """Рецепт как хранится (5.3): без ккал и белка — их считает catalog."""

    id: str
    name: str
    kind: MealKind
    ingredients: tuple[Ingredient, ...]
    steps: tuple[str, ...]
    storage: RecipeStorage | None = None  # у завтраков нет
    is_fish: bool = False
    status: RecipeStatus = "active"


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
    status: RecipeStatus = "active"
    # Множитель порции под цель (8.5): количества и ккал уже умножены.
    scale: float = 1.0


@dataclass(frozen=True, slots=True)
class CatalogIngredient:
    """Ингредиент справочника (фаза 5): одна единица и одна категория."""

    id: int  # рецепты ссылаются по id — переименование их не ломает
    name: str
    unit: str
    category: str
    kcal_100g: float
    protein_100g: float
    source: str | None  # «fdc <fdc_id> <описание>» или этикетка
    grams_per_unit: float | None  # для единиц не г/мл (USDA food_portion)
    store_piece: float | None  # штука в магазине, в unit — «≈ N шт» (6.7)


@dataclass(frozen=True, slots=True)
class CycleSlot:
    """Обед и ужин позиции k в конкретном цикле (Catalog.slot_at)."""

    slot: int  # 1..5 — позиция ротации (cycle_index)
    lunch_id: str
    dinner_id: str


@dataclass(frozen=True, slots=True)
class SlotVersion:
    """Состав позиции цикла с цикла from_cycle (8.4b) — до следующей версии.

    Вариантов может быть несколько: цикл n берёт lunch_ids[n % len] и
    dinner_ids[n % len], обеды и ужины независимо.
    """

    slot: int
    from_cycle: int
    lunch_ids: tuple[str, ...]
    dinner_ids: tuple[str, ...]


def version_at(versions: tuple[SlotVersion, ...], k: int, n: int) -> SlotVersion:
    """Версия позиции k, действующая в цикле n; до первой — первая."""
    own = [v for v in versions if v.slot == k]
    current = own[0]
    for v in own:  # по from_cycle
        if v.from_cycle <= n:
            current = v
    return current


@dataclass(frozen=True, slots=True)
class Catalog:
    """Рецепты, справочник ингредиентов и состав позиций цикла (5.3, 8.4b).

    В API — из БД, в тестах — сид (core/seed.py). Собирает build_catalog.
    """

    recipes: Mapping[str, Recipe]  # завтраки, обеды, ужины; внутри — по номеру id
    ingredients: Mapping[str, CatalogIngredient]  # по названию
    # По позиции, внутри — по from_cycle; у каждой позиции есть версия.
    slot_versions: tuple[SlotVersion, ...]

    @property
    def slot_count(self) -> int:
        return len({v.slot for v in self.slot_versions})

    def slot_at(self, k: int, n: int) -> CycleSlot:
        """Обед и ужин позиции k в цикле n — варианты по кругу."""
        v = version_at(self.slot_versions, k, n)
        return CycleSlot(
            slot=k,
            lunch_id=v.lunch_ids[n % len(v.lunch_ids)],
            dinner_id=v.dinner_ids[n % len(v.dinner_ids)],
        )


@dataclass(frozen=True, slots=True)
class CycleShuffle:
    """С цикла from_cycle — порядок слотов из зерна seed (8.4a).

    Цикл n — дни ленты 5n..5n+4 от якоря. seed None — обратно к кругу 1→5.
    """

    from_cycle: int
    seed: int | None


@dataclass(frozen=True, slots=True)
class PortionScale:
    """С партии, начинающейся не раньше from_date, — порции обедов и ужинов
    под цель kcal_target (8.5); None — без масштаба."""

    from_date: date
    kcal_target: int | None


@dataclass(frozen=True, slots=True)
class Settings:
    meal_anchor_date: date
    cook_count: int
    cook_weekday1: int  # 1..7, пн=1
    cook_weekday2: int | None  # при cook_count=1 игнорируется (JS cookWeekdays)
    shopping_timing: ShoppingTiming
    # Пропущенная готовка (7.7): её окно — пауза без блюд, цикл продолжается
    # с того же блюда со следующей готовки. Один пропуск; прошедший
    # переносится в якорь (scheduling.settle_skip).
    skipped_cook_date: date | None = None
    # Суточная цель (8.1): только показывает отклонение дня, блюда не меняет.
    # None — цели нет.
    daily_kcal_target: int | None = None
    daily_protein_target: int | None = None
    # Перемешивание цикла (8.4a), по from_cycle. Пусто — всегда 1→5.
    cycle_shuffles: tuple[CycleShuffle, ...] = ()
    # Масштаб порций (8.5), по from_date. Пусто — порции как в рецептах.
    portion_scales: tuple[PortionScale, ...] = ()


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
    is_skipped: bool = False  # в паузе пропущенной готовки: блюд нет (7.7)
    # Итог дня минус цель (8.1); None — цели нет или в этот день нет блюд.
    kcal_delta: int | None = None
    protein_delta: int | None = None


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
class SkippedCook:
    """Пропущенная готовка и пауза за ней (7.7)."""

    cook_date: date  # готовки не будет
    pause_start: date  # первый день без блюд
    pause_end: date  # последний день без блюд — он же следующая готовка
    can_undo: bool  # отменить можно, пока день готовки не прошёл


@dataclass(frozen=True, slots=True)
class CycleOrder:
    """Перемешан ли порядок цикла (8.4a) и с какого дня действует последнее
    изменение — None, если уже действует."""

    shuffled: bool
    changes_from: date | None


@dataclass(frozen=True, slots=True)
class PortionScaling:
    """Масштаб порций для UI (8.5): включён ли, под какую цель и с какого
    дня действует последнее изменение — None, если уже действует."""

    enabled: bool
    kcal_target: int | None
    changes_from: date | None


@dataclass(frozen=True, slots=True)
class SlotState:
    """Позиция цикла для UI: последняя версия состава (8.4b) и с какого дня
    она действует — None, если уже действует или правок не было."""

    slot: int
    lunch_ids: tuple[str, ...]
    dinner_ids: tuple[str, ...]
    changes_from: date | None


@dataclass(frozen=True, slots=True)
class AppState:
    today: date  # от него считается поездка в магазин; UI берёт «сегодня» отсюда
    settings: Settings
    schedule: tuple[DayMeals, ...]
    next_shopping_trip: ShoppingTrip
    version: int
    today_position: TodayPosition
    skipped_cook: SkippedCook | None
    can_skip_next_cook: bool
    plan_use: Mapping[str, PlanUse]  # только рецепты с признаком (5.4)
    slots: tuple[SlotState, ...]  # состав цикла — для UI (5.6, 8.4b)
    cycle_order: CycleOrder
    portion_scaling: PortionScaling
    # Масштабированные обеды и ужины из расписания и поездки — для карточек UI.
    scaled_recipes: tuple[Recipe, ...]
