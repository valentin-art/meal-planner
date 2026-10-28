"""Схема SQLite и миграции по PRAGMA user_version (5.2).

Миграция i переводит БД в user_version = i + 1. Каждая идёт в своей
транзакции вместе с user_version: оборванная не оставит половину схемы.
Изменение схемы — новая функция в конце MIGRATIONS, старые не править:
живая БД их уже прошла.
"""

import sqlite3
from collections.abc import Callable

from meal_planner.core.recipes_data import RECIPE_SPECS
from meal_planner.core.seed import seed_ingredients, seed_slots

_SETTINGS = """
CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    meal_anchor_date TEXT NOT NULL,
    cook_count INTEGER NOT NULL,
    cook_weekday1 INTEGER NOT NULL,
    cook_weekday2 INTEGER,
    shopping_timing TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    skipped_cook_date TEXT
)
"""

# Статус и единицы проверяет core/, а не CHECK: набор будет расти (draft в
# 8.6), а CHECK в SQLite меняется только пересозданием таблицы.
_CATALOG = (
    """
    CREATE TABLE ingredients (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        unit TEXT NOT NULL,
        category TEXT NOT NULL,
        kcal_100g REAL NOT NULL CHECK (kcal_100g >= 0),
        protein_100g REAL NOT NULL CHECK (protein_100g >= 0),
        source TEXT,
        grams_per_unit REAL CHECK (grams_per_unit > 0),
        store_piece REAL CHECK (store_piece > 0)
    )
    """,
    """
    CREATE TABLE recipes (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('breakfast', 'lunch', 'dinner')),
        is_fish INTEGER NOT NULL DEFAULT 0 CHECK (is_fish IN (0, 1)),
        status TEXT NOT NULL DEFAULT 'active',
        storage_keep TEXT,
        storage_pan TEXT,
        storage_micro TEXT,
        storage_after TEXT
    )
    """,
    """
    CREATE TABLE recipe_ingredients (
        recipe_id TEXT NOT NULL REFERENCES recipes (id),
        position INTEGER NOT NULL,
        ingredient_id INTEGER NOT NULL REFERENCES ingredients (id),
        qty REAL NOT NULL CHECK (qty > 0),
        PRIMARY KEY (recipe_id, position),
        UNIQUE (recipe_id, ingredient_id)
    )
    """,
    """
    CREATE TABLE recipe_steps (
        recipe_id TEXT NOT NULL REFERENCES recipes (id),
        position INTEGER NOT NULL,
        text TEXT NOT NULL,
        PRIMARY KEY (recipe_id, position)
    )
    """,
    """
    CREATE TABLE cycle_slots (
        slot INTEGER PRIMARY KEY CHECK (slot BETWEEN 1 AND 5),
        lunch_id TEXT NOT NULL REFERENCES recipes (id),
        dinner_id TEXT NOT NULL REFERENCES recipes (id)
    )
    """,
)


def _settings(conn: sqlite3.Connection) -> None:
    conn.execute(_SETTINGS)
    # БД до user_version: таблица могла остаться от версии до 7.7.
    existing = {row[1] for row in conn.execute("PRAGMA table_info(settings)")}
    if "skipped_cook_date" not in existing:
        conn.execute("ALTER TABLE settings ADD COLUMN skipped_cook_date TEXT")


def _catalog(conn: sqlite3.Connection) -> None:
    for statement in _CATALOG:
        conn.execute(statement)
    _seed(conn)


def _seed(conn: sqlite3.Connection) -> None:
    # Сид — часть миграции: каталог заполняется ровно один раз, правки
    # пользователя потом не перезатираются.
    ids = {}
    for i in seed_ingredients():
        ids[i.name] = conn.execute(
            "INSERT INTO ingredients (id, name, unit, category, kcal_100g,"
            " protein_100g, source, grams_per_unit, store_piece)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                i.id,
                i.name,
                i.unit,
                i.category,
                i.kcal_100g,
                i.protein_100g,
                i.source,
                i.grams_per_unit,
                i.store_piece,
            ),
        ).lastrowid
    for recipe in RECIPE_SPECS.values():
        storage = recipe.storage
        conn.execute(
            "INSERT INTO recipes (id, name, kind, is_fish, storage_keep, storage_pan,"
            " storage_micro, storage_after) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                recipe.id,
                recipe.name,
                recipe.kind.value,
                recipe.is_fish,
                storage and storage.keep,
                storage and storage.pan,
                storage and storage.micro,
                storage and storage.after,
            ),
        )
        conn.executemany(
            "INSERT INTO recipe_ingredients (recipe_id, position, ingredient_id, qty)"
            " VALUES (?, ?, ?, ?)",
            [
                (recipe.id, position, ids[i.name], i.qty)
                for position, i in enumerate(recipe.ingredients, start=1)
            ],
        )
        conn.executemany(
            "INSERT INTO recipe_steps (recipe_id, position, text) VALUES (?, ?, ?)",
            [
                (recipe.id, position, text)
                for position, text in enumerate(recipe.steps, start=1)
            ],
        )
    conn.executemany(
        "INSERT INTO cycle_slots (slot, lunch_id, dinner_id) VALUES (?, ?, ?)",
        [(s.slot, s.lunch_id, s.dinner_id) for s in seed_slots()],
    )


def _nutrition_target(conn: sqlite3.Connection) -> None:
    # Суточная цель (8.1); NULL — цели нет, как было до миграции.
    conn.execute("ALTER TABLE settings ADD COLUMN daily_kcal_target INTEGER")
    conn.execute("ALTER TABLE settings ADD COLUMN daily_protein_target INTEGER")


def _cooked(conn: sqlite3.Connection) -> None:
    # Отметки «приготовлено» (8.3b): день готовки партии или день завтрака.
    conn.execute(
        """
        CREATE TABLE cooked (
            day TEXT NOT NULL,
            recipe_id TEXT NOT NULL REFERENCES recipes (id),
            PRIMARY KEY (day, recipe_id)
        )
        """
    )


def _dish_portions(conn: sqlite3.Connection) -> None:
    # Порции блюда вместо плановых (8.3b) — только для списания; ключ как у
    # cooked. Пределы порций проверяет core.
    conn.execute(
        """
        CREATE TABLE dish_portions (
            day TEXT NOT NULL,
            recipe_id TEXT NOT NULL REFERENCES recipes (id),
            portions INTEGER NOT NULL,
            PRIMARY KEY (day, recipe_id)
        )
        """
    )


def _cycle_shuffles(conn: sqlite3.Connection) -> None:
    # Перемешивание цикла (8.4a): с цикла from_cycle — зерно seed (NULL —
    # обратно к 1→5). Вход для recompute, не кэш расписания.
    conn.execute(
        """
        CREATE TABLE cycle_shuffles (
            from_cycle INTEGER PRIMARY KEY,
            seed INTEGER
        )
        """
    )


def _slot_versions(conn: sqlite3.Connection) -> None:
    # Варианты у позиций цикла (8.4b): состав позиции с цикла from_cycle —
    # до следующей версии. Прежние слоты — версии с цикла 0, по одному
    # варианту; cycle_slots больше не нужна.
    conn.execute(
        """
        CREATE TABLE slot_versions (
            slot INTEGER NOT NULL CHECK (slot BETWEEN 1 AND 5),
            from_cycle INTEGER NOT NULL CHECK (from_cycle >= 0),
            kind TEXT NOT NULL CHECK (kind IN ('lunch', 'dinner')),
            position INTEGER NOT NULL,
            recipe_id TEXT NOT NULL REFERENCES recipes (id),
            PRIMARY KEY (slot, from_cycle, kind, position)
        )
        """
    )
    for kind, column in (("lunch", "lunch_id"), ("dinner", "dinner_id")):
        conn.execute(
            "INSERT INTO slot_versions (slot, from_cycle, kind, position, recipe_id)"
            f" SELECT slot, 0, '{kind}', 1, {column} FROM cycle_slots"
        )
    conn.execute("DROP TABLE cycle_slots")


MIGRATIONS: tuple[Callable[[sqlite3.Connection], None], ...] = (
    _settings,  # 1: settings — схема до фазы 5
    _catalog,  # 2: каталог рецептов и сид (5.2)
    _nutrition_target,  # 3: цель по ккал и белку (8.1)
    _cooked,  # 4: отметки «приготовлено» (8.3b)
    _dish_portions,  # 5: порции блюда для списания (8.3b)
    _cycle_shuffles,  # 6: перемешивание цикла (8.4a)
    _slot_versions,  # 7: варианты у позиций цикла (8.4b)
)


class SchemaTooNewError(RuntimeError):
    pass


def user_version(conn: sqlite3.Connection) -> int:
    (version,) = conn.execute("PRAGMA user_version").fetchone()
    return version


def migrate(conn: sqlite3.Connection) -> None:
    """Доводит схему до len(MIGRATIONS); соединение — без открытой транзакции."""
    while True:
        # IMMEDIATE: второй процесс ждёт и не повторит ту же миграцию.
        conn.execute("BEGIN IMMEDIATE")
        try:
            version = user_version(conn)
            if version > len(MIGRATIONS):
                # Старый образ на БД из новой версии (откат после обновления).
                raise SchemaTooNewError(
                    f"Схема БД v{version} новее приложения (v{len(MIGRATIONS)})"
                )
            if version == len(MIGRATIONS):
                conn.rollback()
                return
            MIGRATIONS[version](conn)
            conn.execute(f"PRAGMA user_version = {version + 1}")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
