import shutil
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta

import pytest

from meal_planner.core.models import Settings
from meal_planner.core.recipes_data import RECIPE_SPECS
from meal_planner.db import backup as backup_mod
from meal_planner.db.backup import BackupError, backup, main
from meal_planner.db.repository import Repository, StoredSettings
from meal_planner.db.schema import MIGRATIONS, user_version

SETTINGS = Settings(date(2026, 11, 5), 2, 3, 6, "same")
NOW = datetime(2026, 10, 26, 3, 30, 0)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "data" / "mealplan.db"
    repo = Repository(path)
    repo.save_settings(SETTINGS)
    repo.save_settings(SETTINGS)  # version 2 — проверяется, что версия тоже на месте
    return path


def test_restore_from_scratch(db, tmp_path):
    copy = backup(db, tmp_path / "backups", keep=3, now=NOW)
    assert copy.name == "mealplan-20261026-033000.db"

    # «С нуля»: новый каталог данных, в нём только копия под именем БД.
    fresh = tmp_path / "fresh" / "mealplan.db"
    fresh.parent.mkdir()
    shutil.copy(copy, fresh)
    assert Repository(fresh).get_settings() == StoredSettings(SETTINGS, 2)
    # Каталог (5.2) и версия схемы тоже в копии: миграции не перезапускаются.
    with closing(sqlite3.connect(fresh)) as conn:
        assert user_version(conn) == len(MIGRATIONS)
        assert conn.execute("SELECT count(*) FROM recipes").fetchone() == (
            len(RECIPE_SPECS),
        )
        assert conn.execute("SELECT count(*) FROM cycle_slots").fetchone() == (5,)


def test_keeps_last_n_and_ignores_other_files(db, tmp_path):
    dest = tmp_path / "backups"
    dest.mkdir()
    (dest / "notes.txt").write_text("не трогать")
    made = [backup(db, dest, keep=2, now=NOW + timedelta(days=i)) for i in range(4)]
    assert sorted(p.name for p in dest.iterdir()) == sorted(
        [made[2].name, made[3].name, "notes.txt"]
    )


def test_missing_db_is_error_and_not_created(tmp_path):
    db = tmp_path / "mealplan.db"
    with pytest.raises(BackupError):
        backup(db, tmp_path / "backups", keep=3, now=NOW)
    assert not db.exists()


def test_failed_integrity_check_leaves_no_files(db, tmp_path, monkeypatch):
    monkeypatch.setattr(backup_mod, "integrity", lambda conn: "row 1 missing")
    dest = tmp_path / "backups"
    with pytest.raises(BackupError, match="integrity_check"):
        backup(db, dest, keep=3, now=NOW)
    assert list(dest.iterdir()) == []


def test_cli_defaults_to_backups_next_to_db(db, monkeypatch, capsys):
    monkeypatch.setenv("MEALPLAN_DB_PATH", str(db))
    main([])
    printed = capsys.readouterr().out.strip()
    assert printed.startswith(str(db.parent / "backups" / "mealplan-"))


def test_cli_missing_db_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.setenv("MEALPLAN_DB_PATH", str(tmp_path / "none.db"))
    with pytest.raises(SystemExit) as e:
        main(["--dir", str(tmp_path / "b")])
    assert "Нет файла БД" in str(e.value.code)
