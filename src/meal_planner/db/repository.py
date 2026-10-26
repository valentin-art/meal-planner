"""SQLite-хранилище настроек: одна строка settings, больше ничего.

Расписание и список покупок не хранятся — их всегда считает planner.recompute.
"""

import os
import sqlite3
from collections.abc import Generator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from meal_planner.core.models import Settings

DEFAULT_DB_PATH = Path("data/mealplan.db")

_SCHEMA = """
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

# Колонки, добавленные после первой версии схемы: (имя, определение).
_ADDED_COLUMNS = (("skipped_cook_date", "TEXT"),)  # 7.7


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


def _migrate(conn: sqlite3.Connection) -> None:
    # CREATE TABLE IF NOT EXISTS не трогает таблицу из старой версии —
    # недостающие колонки добавляются здесь (NULL у существующей строки).
    existing = {row[1] for row in conn.execute("PRAGMA table_info(settings)")}
    for name, definition in _ADDED_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE settings ADD COLUMN {name} {definition}")


class SettingsRepository:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_SCHEMA)
            _migrate(conn)

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        # Новое соединение на каждый вызов: FastAPI гоняет sync-эндпоинты в пуле
        # потоков, а sqlite3-соединение между потоками делить нельзя.
        # Внешний with закрывает соединение, внутренний — commit/rollback.
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
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

    def save_settings(self, settings: Settings) -> int:
        """Сохраняет настройки целиком и возвращает новую версию (1, 2, ...)."""
        with self._connect() as conn:
            (version,) = conn.execute(_UPSERT, _row(settings)).fetchone()
        return version
