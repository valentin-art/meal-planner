import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

from meal_planner.core.models import Settings
from meal_planner.db.repository import (
    DEFAULT_DB_PATH,
    SettingsRepository,
    StoredSettings,
    db_path_from_env,
)

SETTINGS = Settings(date(2026, 11, 5), 2, 3, 6, "same")


def test_creates_directory_and_file_on_first_use(tmp_path):
    path = tmp_path / "nested" / "dir" / "mealplan.db"
    repo = SettingsRepository(path)
    assert path.is_file()
    assert repo.get_settings() is None


def test_save_and_get_roundtrip(tmp_path):
    repo = SettingsRepository(tmp_path / "db.sqlite")
    single = Settings(date(2027, 3, 25), 1, 7, None, "before")
    assert repo.save_settings(single) == 1
    assert repo.get_settings() == StoredSettings(settings=single, version=1)


def test_version_increments_on_every_save(tmp_path):
    repo = SettingsRepository(tmp_path / "db.sqlite")
    assert [repo.save_settings(SETTINGS) for _ in range(3)] == [1, 2, 3]
    assert repo.get_settings().version == 3


def test_survives_restart(tmp_path):
    path = tmp_path / "db.sqlite"
    SettingsRepository(path).save_settings(SETTINGS)
    assert SettingsRepository(path).get_settings() == StoredSettings(SETTINGS, 1)


def test_single_row(tmp_path):
    path = tmp_path / "db.sqlite"
    repo = SettingsRepository(path)
    repo.save_settings(SETTINGS)
    repo.save_settings(Settings(date(2026, 12, 1), 1, 7, None, "before"))
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT count(*) FROM settings").fetchone() == (1,)


def test_init_settings_writes_only_into_empty_db(tmp_path):
    repo = SettingsRepository(tmp_path / "db.sqlite")
    assert repo.init_settings(SETTINGS) == StoredSettings(SETTINGS, 1)
    other = Settings(date(2026, 12, 1), 1, 7, None, "before")
    assert repo.init_settings(other) == StoredSettings(SETTINGS, 1)  # не тронуто
    assert repo.get_settings() == StoredSettings(SETTINGS, 1)


def test_init_settings_keeps_saved(tmp_path):
    repo = SettingsRepository(tmp_path / "db.sqlite")
    repo.save_settings(SETTINGS)
    repo.save_settings(SETTINGS)
    other = Settings(date(2026, 12, 1), 1, 7, None, "before")
    assert repo.init_settings(other) == StoredSettings(SETTINGS, 2)


def test_db_path_from_env(monkeypatch, tmp_path):
    monkeypatch.delenv("MEALPLAN_DB_PATH", raising=False)
    assert db_path_from_env() == DEFAULT_DB_PATH == Path("data/mealplan.db")
    monkeypatch.setenv("MEALPLAN_DB_PATH", str(tmp_path / "x.db"))
    assert db_path_from_env() == tmp_path / "x.db"


def test_skipped_cook_roundtrip(tmp_path):
    repo = SettingsRepository(tmp_path / "db.sqlite")
    skipped = Settings(date(2026, 11, 5), 2, 3, 6, "same", date(2026, 11, 11))
    repo.save_settings(skipped)
    assert repo.get_settings() == StoredSettings(skipped, 1)
    repo.save_settings(SETTINGS)  # пропуск снят — NULL в БД
    assert repo.get_settings() == StoredSettings(SETTINGS, 2)


def test_migrates_db_from_before_skip(tmp_path):
    # Таблица до 7.7: без skipped_cook_date, со строкой настроек.
    path = tmp_path / "old.db"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(
            "CREATE TABLE settings (id INTEGER PRIMARY KEY CHECK (id = 1),"
            " meal_anchor_date TEXT NOT NULL, cook_count INTEGER NOT NULL,"
            " cook_weekday1 INTEGER NOT NULL, cook_weekday2 INTEGER,"
            " shopping_timing TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1)"
        )
        conn.execute(
            "INSERT INTO settings VALUES (1, '2026-11-05', 2, 3, 6, 'same', 4)"
        )
    repo = SettingsRepository(path)
    assert repo.get_settings() == StoredSettings(SETTINGS, 4)
    assert repo.save_settings(SETTINGS) == 5
    SettingsRepository(path)  # повторное открытие — колонка уже есть
