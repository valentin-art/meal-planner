"""5.2: схема каталога, миграции по user_version и сид из модулей-констант."""

import json
import sqlite3
from contextlib import closing
from dataclasses import astuple, replace
from datetime import date
from pathlib import Path

import pytest

from meal_planner.core.models import Settings, SlotVersion
from meal_planner.core.nutrition import GRAMS_PER_UNIT, PER_100G
from meal_planner.core.recipes_data import RECIPE_SPECS, STORE_PIECES
from meal_planner.core.seed import (
    DINNER_IDS,
    LUNCH_IDS,
    SEED_CATALOG,
    seed_ingredients,
)
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
    "slot_versions",  # 8.4b, вместо cycle_slots
}
ALL_TABLES = {
    "settings",
    "cooked",  # 8.3b
    "dish_portions",  # 8.3b
    "cycle_shuffles",  # 8.4a
    "portion_scales",  # 8.5
    "cook_usage",  # 10.2
} | CATALOG_TABLES


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
        assert user_version(conn) == len(MIGRATIONS) == 9
    assert tables(db) == ALL_TABLES


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
            "SELECT l.slot, l.recipe_id, d.recipe_id FROM slot_versions l"
            " JOIN slot_versions d ON d.slot = l.slot AND d.kind = 'dinner'"
            " WHERE l.kind = 'lunch' AND l.from_cycle = 0 ORDER BY l.slot"
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
    assert tables(path) == ALL_TABLES
    assert len(dump_recipes(path)) == len(RECIPE_SPECS)


def test_nutrition_target_migration_keeps_settings(tmp_path, monkeypatch):
    # БД на v2 (до 8.1) с сохранёнными настройками: цели появляются пустыми.
    path = tmp_path / "v2.db"
    monkeypatch.setattr(schema, "MIGRATIONS", MIGRATIONS[:2])
    Repository(path)
    monkeypatch.undo()
    with connect(path) as conn, conn:
        conn.execute(
            "INSERT INTO settings VALUES (1, '2026-11-05', 2, 3, 6, 'same', 7, NULL)"
        )
    repo = Repository(path)
    with connect(path) as conn:
        assert user_version(conn) == len(MIGRATIONS)
    assert repo.get_settings() == StoredSettings(SETTINGS, 7)


def test_cycle_shuffles_migration_keeps_settings(tmp_path, monkeypatch):
    # БД на v5 (до 8.4a): перемешивания нет — порядок 1→5, как раньше.
    path = tmp_path / "v5.db"
    monkeypatch.setattr(schema, "MIGRATIONS", MIGRATIONS[:5])
    Repository(path)
    monkeypatch.undo()
    with connect(path) as conn, conn:
        conn.execute(
            "INSERT INTO settings VALUES (1, '2026-11-05', 2, 3, 6, 'same', 7, NULL,"
            " NULL, NULL)"
        )
    repo = Repository(path)
    with connect(path) as conn:
        assert user_version(conn) == len(MIGRATIONS)
    assert repo.get_settings() == StoredSettings(SETTINGS, 7)


def test_slot_versions_migration_keeps_slots(tmp_path, monkeypatch):
    # БД на v6 (до 8.4b) с правленым слотом: он переезжает версией с цикла 0.
    path = tmp_path / "v6.db"
    monkeypatch.setattr(schema, "MIGRATIONS", MIGRATIONS[:6])
    Repository(path)
    monkeypatch.undo()
    with connect(path) as conn, conn:
        conn.execute("UPDATE cycle_slots SET lunch_id = 'l4' WHERE slot = 1")
    catalog = Repository(path).get_catalog()
    assert "cycle_slots" not in tables(path)
    assert catalog.slot_versions[0] == SlotVersion(1, 0, ("l4",), ("d1",))
    assert catalog.slot_versions[1:] == SEED_CATALOG.slot_versions[1:]


def test_portion_scales_migration_keeps_settings(tmp_path, monkeypatch):
    # БД на v7 (до 8.5): масштаба нет — порции как в рецептах.
    path = tmp_path / "v7.db"
    monkeypatch.setattr(schema, "MIGRATIONS", MIGRATIONS[:7])
    Repository(path)
    monkeypatch.undo()
    with connect(path) as conn, conn:
        conn.execute(
            "INSERT INTO settings VALUES (1, '2026-11-05', 2, 3, 6, 'same', 7, NULL,"
            " 2700, NULL)"
        )
    # portion_scales — дефолт (): порции как в рецептах.
    assert Repository(path).get_settings() == StoredSettings(
        replace(SETTINGS, daily_kcal_target=2700), 7
    )


def test_cook_usage_migration_keeps_marks(tmp_path, monkeypatch):
    # БД на v8 (до 10.2) с отметками и порциями: готовили в день ключа.
    path = tmp_path / "v8.db"
    monkeypatch.setattr(schema, "MIGRATIONS", MIGRATIONS[:8])
    Repository(path).save_settings(SETTINGS)
    monkeypatch.undo()
    with connect(path) as conn, conn:
        conn.executemany(
            "INSERT INTO cooked (day, recipe_id) VALUES (?, ?)",
            [("2026-11-07", "l5"), ("2026-11-09", "b1")],
        )
        conn.execute("INSERT INTO dish_portions VALUES ('2026-11-07', 'l5', 3)")
    state = Repository(path).get_cook_state()
    assert state.cooked == {
        (date(2026, 11, 7), "l5"): date(2026, 11, 7),
        (date(2026, 11, 9), "b1"): date(2026, 11, 9),
    }
    assert state.portions == {(date(2026, 11, 7), "l5"): 3}
    assert state.usage == {}


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
    assert tables(path) == ALL_TABLES


def test_db_from_newer_app_is_refused(db):
    with connect(db) as conn:
        conn.execute(f"PRAGMA user_version = {len(MIGRATIONS) + 1}")
    with pytest.raises(SchemaTooNewError, match="новее приложения"):
        Repository(db)


def test_foreign_keys_are_enforced(db):
    repo = Repository(db)
    with pytest.raises(sqlite3.IntegrityError), repo._connect() as conn:
        conn.execute(
            "INSERT INTO slot_versions (slot, from_cycle, kind, position, recipe_id)"
            " VALUES (1, 2, 'lunch', 1, 'нет')"
        )
