"""5.2: схема каталога, миграции по user_version и сид из модулей-констант."""

import json
import sqlite3
from contextlib import closing
from dataclasses import astuple
from datetime import date
from pathlib import Path

import pytest

from meal_planner.core.models import Settings
from meal_planner.core.nutrition import GRAMS_PER_UNIT, PER_100G
from meal_planner.core.recipes_data import RECIPE_SPECS, STORE_PIECES
from meal_planner.core.seed import DINNER_IDS, LUNCH_IDS, seed_ingredients
from meal_planner.db import schema
from meal_planner.db.repository import Repository, StoredSettings
from meal_planner.db.schema import MIGRATIONS, SchemaTooNewError, user_version

SNAPSHOT = Path(__file__).parent / "recipes_snapshot.json"
SETTINGS = Settings(date(2026, 11, 5), 2, 3, 6, "same")
CATALOG_TABLES = {
    "ingredients",
    "recipes",
    "recipe_ingredients",
    "recipe_steps",
    "cycle_slots",
}


def connect(path: Path) -> closing[sqlite3.Connection]:
    return closing(sqlite3.connect(path))


def tables(path: Path) -> set[str]:
    with connect(path) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        return {name for (name,) in rows}


def dump_recipes(path: Path) -> dict:
    """Рецепты из БД в форме tests/recipes_snapshot.json, без ккал и белка."""
    with connect(path) as conn:
        recipes = {}
        for row in conn.execute(
            "SELECT id, name, kind, is_fish, status, storage_keep, storage_pan,"
            " storage_micro, storage_after FROM recipes"
        ):
            rid, name, kind, is_fish, status, keep, pan, micro, after = row
            ingredients = conn.execute(
                "SELECT i.name, ri.qty, i.unit, i.category FROM recipe_ingredients ri"
                " JOIN ingredients i ON i.id = ri.ingredient_id"
                " WHERE ri.recipe_id = ? ORDER BY ri.position",
                (rid,),
            ).fetchall()
            steps = conn.execute(
                "SELECT text FROM recipe_steps WHERE recipe_id = ? ORDER BY position",
                (rid,),
            ).fetchall()
            storage = (
                None
                if keep is None
                else {"keep": keep, "pan": pan, "micro": micro, "after": after}
            )
            recipes[rid] = {
                "id": rid,
                "name": name,
                "kind": kind,
                "ingredients": [
                    {"name": n, "qty": q, "unit": u, "category": c}
                    for n, q, u, c in ingredients
                ],
                "steps": [text for (text,) in steps],
                "storage": storage,
                "is_fish": bool(is_fish),
                "status": status,
            }
        return recipes


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "mealplan.db"
    Repository(path)
    return path


def test_fresh_db_is_at_latest_version(db):
    with connect(db) as conn:
        assert user_version(conn) == len(MIGRATIONS) == 2
    assert tables(db) == {"settings"} | CATALOG_TABLES


def test_seeded_recipes_match_snapshot(db):
    # Ккал и белок в БД не хранятся — их считает core/ (5.1).
    snapshot = json.loads(SNAPSHOT.read_text("utf-8"))
    for recipe in snapshot.values():
        del recipe["kcal"], recipe["protein"]
    assert dump_recipes(db) == snapshot


def test_seeded_ingredients_match_constants(db):
    with connect(db) as conn:
        rows = conn.execute(
            "SELECT id, name, unit, category, kcal_100g, protein_100g, source,"
            " grams_per_unit, store_piece FROM ingredients ORDER BY id"
        ).fetchall()
    assert rows == [astuple(i) for i in seed_ingredients()]
    assert [name for _, name, *_ in rows] == list(PER_100G)
    for _, name, unit, _, kcal, protein, source, grams, piece in rows:
        assert (kcal, protein, source) == PER_100G[name]
        assert grams == GRAMS_PER_UNIT.get((name, unit))
        assert piece == STORE_PIECES.get((name, unit))
        assert (grams is None) == (unit in ("г", "мл"))


def test_seeded_slots_and_statuses(db):
    with connect(db) as conn:
        slots = conn.execute(
            "SELECT slot, lunch_id, dinner_id FROM cycle_slots ORDER BY slot"
        ).fetchall()
        statuses = dict(conn.execute("SELECT id, status FROM recipes"))
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert slots == [(k, LUNCH_IDS[k - 1], DINNER_IDS[k - 1]) for k in range(1, 6)]
    assert statuses == dict.fromkeys(RECIPE_SPECS, "active")


def test_seed_runs_once(db):
    # Правка пользователя не перезатирается при следующем запуске.
    with connect(db) as conn, conn:
        conn.execute("UPDATE recipes SET name = 'Своё' WHERE id = 'l1'")
    Repository(db)
    with connect(db) as conn:
        assert conn.execute("SELECT name FROM recipes WHERE id = 'l1'").fetchone() == (
            "Своё",
        )
        assert conn.execute("SELECT count(*) FROM recipes").fetchone() == (
            len(RECIPE_SPECS),
        )


def test_migrates_live_db_before_phase_5(tmp_path):
    # Живая БД до 5.2: settings со строкой, user_version = 0.
    path = tmp_path / "live.db"
    with connect(path) as conn, conn:
        conn.execute(schema._SETTINGS)
        conn.execute(
            "INSERT INTO settings VALUES (1, '2026-11-05', 2, 3, 6, 'same', 7, NULL)"
        )
    repo = Repository(path)
    assert repo.get_settings() == StoredSettings(SETTINGS, 7)
    assert tables(path) == {"settings"} | CATALOG_TABLES
    assert len(dump_recipes(path)) == len(RECIPE_SPECS)


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    def broken(conn):
        schema._catalog(conn)
        raise RuntimeError("сбой посреди миграции")

    monkeypatch.setattr(schema, "MIGRATIONS", (schema._settings, broken))
    path = tmp_path / "mealplan.db"
    with pytest.raises(RuntimeError, match="сбой"):
        Repository(path)
    # Первая миграция осталась, от второй — ничего.
    with connect(path) as conn:
        assert user_version(conn) == 1
    assert tables(path) == {"settings"}

    monkeypatch.undo()
    Repository(path)
    assert tables(path) == {"settings"} | CATALOG_TABLES


def test_db_from_newer_app_is_refused(db):
    with connect(db) as conn:
        conn.execute(f"PRAGMA user_version = {len(MIGRATIONS) + 1}")
    with pytest.raises(SchemaTooNewError, match="новее приложения"):
        Repository(db)


def test_foreign_keys_are_enforced(db):
    repo = Repository(db)
    with pytest.raises(sqlite3.IntegrityError), repo._connect() as conn:
        conn.execute(
            "INSERT INTO cycle_slots (slot, lunch_id, dinner_id)"
            " VALUES (1, 'нет', 'd1') ON CONFLICT (slot) DO UPDATE"
            " SET lunch_id = excluded.lunch_id"
        )
