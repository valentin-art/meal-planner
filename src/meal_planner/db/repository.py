"""SQLite-хранилище: строка settings и каталог рецептов (фаза 5).

Схема и миграции — db/schema.py. Расписание и список покупок не хранятся —
их всегда считает planner.recompute.
"""

import os
import sqlite3
from collections.abc import Generator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from meal_planner.core.catalog import build_catalog
from meal_planner.core.models import (
    Catalog,
    CatalogIngredient,
    CycleSlot,
    Ingredient,
    MealKind,
    RecipeSpec,
    RecipeStorage,
    Settings,
)
from meal_planner.db.schema import migrate

DEFAULT_DB_PATH = Path("data/mealplan.db")

_UPSERT = """
INSERT INTO settings
    (id, meal_anchor_date, cook_count, cook_weekday1, cook_weekday2, shopping_timing,
     skipped_cook_date)
VALUES (1, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    meal_anchor_date = excluded.meal_anchor_date,
    cook_count = excluded.cook_count,
    cook_weekday1 = excluded.cook_weekday1,
    cook_weekday2 = excluded.cook_weekday2,
    shopping_timing = excluded.shopping_timing,
    skipped_cook_date = excluded.skipped_cook_date,
    version = version + 1
RETURNING version
"""

_INSERT_IF_ABSENT = """
INSERT INTO settings
    (id, meal_anchor_date, cook_count, cook_weekday1, cook_weekday2, shopping_timing,
     skipped_cook_date)
VALUES (1, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO NOTHING
"""


def db_path_from_env() -> Path:
    return Path(os.environ.get("MEALPLAN_DB_PATH", DEFAULT_DB_PATH))


@dataclass(frozen=True, slots=True)
class StoredSettings:
    settings: Settings
    version: int


def _row(settings: Settings) -> tuple:
    return (
        settings.meal_anchor_date.isoformat(),
        settings.cook_count,
        settings.cook_weekday1,
        settings.cook_weekday2,
        settings.shopping_timing,
        settings.skipped_cook_date and settings.skipped_cook_date.isoformat(),
    )


def _read(conn: sqlite3.Connection) -> StoredSettings | None:
    row = conn.execute(
        "SELECT meal_anchor_date, cook_count, cook_weekday1, cook_weekday2,"
        " shopping_timing, skipped_cook_date, version FROM settings WHERE id = 1"
    ).fetchone()
    if row is None:
        return None
    anchor, cook_count, wd1, wd2, timing, skipped, version = row
    settings = Settings(
        date.fromisoformat(anchor),
        cook_count,
        wd1,
        wd2,
        timing,
        skipped and date.fromisoformat(skipped),
    )
    return StoredSettings(settings=settings, version=version)


def _read_catalog(conn: sqlite3.Connection) -> Catalog:
    ingredients = [
        CatalogIngredient(*row)
        for row in conn.execute(
            "SELECT name, unit, category, kcal_100g, protein_100g, source,"
            " grams_per_unit, store_piece FROM ingredients ORDER BY id"
        )
    ]
    lines: dict[str, list[Ingredient]] = {}
    for recipe_id, name, qty, unit, category in conn.execute(
        "SELECT ri.recipe_id, i.name, ri.qty, i.unit, i.category"
        " FROM recipe_ingredients ri JOIN ingredients i ON i.id = ri.ingredient_id"
        " ORDER BY ri.recipe_id, ri.position"
    ):
        lines.setdefault(recipe_id, []).append(Ingredient(name, qty, unit, category))
    steps: dict[str, list[str]] = {}
    for recipe_id, text in conn.execute(
        "SELECT recipe_id, text FROM recipe_steps ORDER BY recipe_id, position"
    ):
        steps.setdefault(recipe_id, []).append(text)
    recipes = [
        RecipeSpec(
            id=rid,
            name=name,
            kind=MealKind(kind),
            ingredients=tuple(lines.get(rid, ())),
            steps=tuple(steps.get(rid, ())),
            storage=None if keep is None else RecipeStorage(keep, pan, micro, after),
            is_fish=bool(is_fish),
        )
        for rid, name, kind, is_fish, keep, pan, micro, after in conn.execute(
            "SELECT id, name, kind, is_fish, storage_keep, storage_pan,"
            " storage_micro, storage_after FROM recipes"
        )
    ]
    slots = [
        CycleSlot(*row)
        for row in conn.execute("SELECT slot, lunch_id, dinner_id FROM cycle_slots")
    ]
    # Порядок рецептов задаёт build_catalog, не rowid (его меняет VACUUM).
    return build_catalog(ingredients, recipes, slots)


class SettingsRepository:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            migrate(conn)

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        # Новое соединение на каждый вызов: FastAPI гоняет sync-эндпоинты в пуле
        # потоков, а sqlite3-соединение между потоками делить нельзя.
        # Внешний with закрывает соединение, внутренний — commit/rollback.
        with closing(sqlite3.connect(self.db_path)) as conn:
            # Внешние ключи в SQLite включаются на каждое соединение.
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:
                yield conn

    def get_settings(self) -> StoredSettings | None:
        """None, если ещё ничего не сохранено — дефолты подставляет вызывающий."""
        with self._connect() as conn:
            return _read(conn)

    def init_settings(self, defaults: Settings) -> StoredSettings:
        """Сохраняет defaults, только если ещё ничего нет; возвращает то, что в БД.

        Вставка и чтение — в одной транзакции: при одновременных первых
        запросах оба получат одну и ту же строку.
        """
        with self._connect() as conn:
            conn.execute(_INSERT_IF_ABSENT, _row(defaults))
            stored = _read(conn)
        assert stored is not None
        return stored

    def get_catalog(self) -> Catalog:
        """Рецепты, справочник и слоты — для recompute (5.3)."""
        with self._connect() as conn:
            return _read_catalog(conn)

    def save_settings(self, settings: Settings) -> int:
        """Сохраняет настройки целиком и возвращает новую версию (1, 2, ...)."""
        with self._connect() as conn:
            (version,) = conn.execute(_UPSERT, _row(settings)).fetchone()
        return version
