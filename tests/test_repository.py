import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from meal_planner.core.catalog import spec_of
from meal_planner.core.cooking import CookState
from meal_planner.core.models import (
    CatalogIngredient,
    CycleShuffle,
    Ingredient,
    PortionScale,
    Settings,
    SlotVersion,
)
from meal_planner.core.seed import SEED_CATALOG
from meal_planner.db.repository import (
    DEFAULT_DB_PATH,
    Repository,
    StoredSettings,
    db_path_from_env,
)

SETTINGS = Settings(date(2026, 11, 5), 2, 3, 6, "same")


def test_creates_directory_and_file_on_first_use(tmp_path):
    path = tmp_path / "nested" / "dir" / "mealplan.db"
    repo = Repository(path)
    assert path.is_file()
    assert repo.get_settings() is None


def test_save_and_get_roundtrip(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    single = Settings(date(2027, 3, 25), 1, 7, None, "before")
    assert repo.save_settings(single) == 1
    assert repo.get_settings() == StoredSettings(settings=single, version=1)


def test_version_increments_on_every_save(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    assert [repo.save_settings(SETTINGS) for _ in range(3)] == [1, 2, 3]
    assert repo.get_settings() == StoredSettings(SETTINGS, 3)


def test_survives_restart(tmp_path):
    path = tmp_path / "db.sqlite"
    Repository(path).save_settings(SETTINGS)
    assert Repository(path).get_settings() == StoredSettings(SETTINGS, 1)


def test_single_row(tmp_path):
    path = tmp_path / "db.sqlite"
    repo = Repository(path)
    repo.save_settings(SETTINGS)
    repo.save_settings(Settings(date(2026, 12, 1), 1, 7, None, "before"))
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT count(*) FROM settings").fetchone() == (1,)


def test_init_settings_writes_only_into_empty_db(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    assert repo.init_settings(SETTINGS) == StoredSettings(SETTINGS, 1)
    other = Settings(date(2026, 12, 1), 1, 7, None, "before")
    assert repo.init_settings(other) == StoredSettings(SETTINGS, 1)  # не тронуто
    assert repo.get_settings() == StoredSettings(SETTINGS, 1)


def test_init_settings_keeps_saved(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
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
    repo = Repository(tmp_path / "db.sqlite")
    skipped = Settings(date(2026, 11, 5), 2, 3, 6, "same", date(2026, 11, 11))
    repo.save_settings(skipped)
    assert repo.get_settings() == StoredSettings(skipped, 1)
    repo.save_settings(SETTINGS)  # пропуск снят — NULL в БД
    assert repo.get_settings() == StoredSettings(SETTINGS, 2)


def test_nutrition_target_roundtrip(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    target = replace(SETTINGS, daily_kcal_target=2000, daily_protein_target=100)
    repo.save_settings(target)
    assert repo.get_settings() == StoredSettings(target, 1)
    repo.save_settings(SETTINGS)  # цели сняты — NULL в БД
    assert repo.get_settings() == StoredSettings(SETTINGS, 2)


def test_cycle_shuffles_roundtrip(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    shuffled = replace(
        SETTINGS, cycle_shuffles=(CycleShuffle(2, 42), CycleShuffle(5, None))
    )
    repo.save_settings(shuffled)
    assert repo.get_settings() == StoredSettings(shuffled, 1)
    repo.save_settings(SETTINGS)  # строки заменяются целиком
    assert repo.get_settings() == StoredSettings(SETTINGS, 2)


def test_portion_scales_roundtrip(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    scaled = replace(
        SETTINGS,
        daily_kcal_target=2700,
        portion_scales=(
            PortionScale(date(2026, 11, 12), 2700),
            PortionScale(date(2026, 11, 19), None),
        ),
    )
    repo.save_settings(scaled)
    assert repo.get_settings() == StoredSettings(scaled, 1)
    repo.save_settings(SETTINGS)  # строки заменяются целиком
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
    repo = Repository(path)
    assert repo.get_settings() == StoredSettings(SETTINGS, 4)
    assert repo.save_settings(SETTINGS) == 5
    Repository(path)  # повторное открытие — колонка уже есть


# --- Запись каталога (5.4) ---


def test_catalog_writes_bump_version(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    repo.save_settings(SETTINGS)
    l1 = SEED_CATALOG.recipes["l1"]
    spec = replace(
        spec_of(l1),
        id="l6",
        name="Копия",
        ingredients=l1.ingredients[:2],
        steps=("Один",),
    )
    assert repo.save_recipe(spec) == 2
    versions = (
        SlotVersion(1, 0, ("l1",), ("d1",)),
        SlotVersion(1, 2, ("l1", "l6"), ("d1",)),
        *SEED_CATALOG.slot_versions[1:],
    )
    assert repo.save_slot_versions(versions) == 3
    eggs = SEED_CATALOG.ingredients["яйца"]
    assert repo.save_ingredient(replace(eggs, name="яйца куриные")) == 4

    catalog = repo.get_catalog()
    copy = catalog.recipes["l6"]
    assert (copy.name, copy.steps, copy.ingredients) == (
        "Копия",
        ("Один",),
        l1.ingredients[:2],
    )
    assert catalog.slot_versions == versions
    # Рецепты ссылаются на ингредиент по id — переименование видно в них.
    assert "яйца куриные" in {i.name for i in catalog.recipes["b1"].ingredients}

    # Правка заменяет строки и шаги целиком.
    repo.save_recipe(replace(spec, ingredients=l1.ingredients[:1], status="archived"))
    copy = repo.get_catalog().recipes["l6"]
    assert (copy.ingredients, copy.status) == (l1.ingredients[:1], "archived")


def test_delete_recipe(tmp_path):
    # Отброшенный черновик (8.6a) — без строк и шагов.
    repo = Repository(tmp_path / "db.sqlite")
    repo.save_settings(SETTINGS)
    l1 = SEED_CATALOG.recipes["l1"]
    repo.save_recipe(replace(spec_of(l1), id="l6", status="draft"))
    assert repo.delete_recipe("l6") == 3
    assert "l6" not in repo.get_catalog().recipes
    with closing(sqlite3.connect(repo.db_path)) as conn:
        for table in ("recipe_ingredients", "recipe_steps"):
            count = conn.execute(
                f"SELECT count(*) FROM {table} WHERE recipe_id = 'l6'"
            ).fetchone()
            assert count == (0,)


def test_save_proposal_writes_ingredients_and_recipe(tmp_path):
    # Черновик от Assist с новым ингредиентом (8.6b) — одна версия.
    repo = Repository(tmp_path / "db.sqlite")
    repo.save_settings(SETTINGS)
    tofu = CatalogIngredient(
        len(SEED_CATALOG.ingredients) + 1,
        "тофу",
        "г",
        "Белок",
        76,
        8.08,
        "Assist: оценка модели",
        None,
        None,
    )
    l1 = spec_of(SEED_CATALOG.recipes["l1"])
    line = Ingredient("тофу", 100, "г", "Белок")
    draft = replace(l1, id="l6", ingredients=(line,), status="draft")
    assert repo.save_proposal(draft, (tofu,)) == 2
    catalog = repo.get_catalog()
    assert catalog.ingredients["тофу"] == tofu
    assert catalog.recipes["l6"].ingredients == (line,)


def test_catalog_write_without_settings_writes_nothing(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    renamed = replace(spec_of(SEED_CATALOG.recipes["l1"]), name="Другое")
    with pytest.raises(RuntimeError, match="версию"):
        repo.save_recipe(renamed)
    assert repo.get_catalog().recipes["l1"].name == SEED_CATALOG.recipes["l1"].name


def plan(day, recipe_id):
    return day, recipe_id, "plan"


def extra(day, recipe_id):
    return day, recipe_id, "extra"


def test_cooked_marks(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    repo.save_settings(SETTINGS)
    day, other = date(2026, 11, 11), date(2026, 11, 12)
    assert repo.get_cook_state().cooked == {}
    assert repo.record_cook(plan(day, "l3"), day, {}) == 2
    assert repo.record_cook(plan(other, "b1"), other, {}) == 3
    # Тот же рецепт в тот же день вне очереди — свой ключ (10.6).
    assert repo.record_cook(extra(day, "l3"), day, {}) == 4
    assert repo.get_cook_state().cooked == {
        plan(day, "l3"): day,
        plan(other, "b1"): other,
        extra(day, "l3"): day,
    }
    with pytest.raises(sqlite3.IntegrityError):
        repo.record_cook(plan(day, "l3"), day, {})
    stored = repo.get_settings()
    assert stored is not None and stored.version == 4  # повтор не записан
    assert repo.clear_cook(plan(day, "l3")) == 5
    assert repo.get_cook_state().cooked == {
        plan(other, "b1"): other,
        extra(day, "l3"): day,
    }


def test_dish_portions(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    repo.save_settings(SETTINGS)
    key = plan(date(2026, 11, 11), "l3")
    assert repo.get_dish_portions() == {}
    assert repo.set_dish_portions(key, 3) == 2
    assert repo.set_dish_portions(key, 2) == 3  # заменяет
    assert repo.set_dish_portions(extra(date(2026, 11, 11), "l3"), 1) == 4
    assert repo.get_dish_portions() == {key: 2, extra(date(2026, 11, 11), "l3"): 1}


AVOCADO, SPINACH, PASTA = (
    SEED_CATALOG.ingredients[name].id
    for name in ("авокадо", "шпинат", "паста цельнозерновая")
)


def test_cook_usage_roundtrip(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    repo.save_settings(SETTINGS)
    key = extra(date(2026, 11, 9), "l3")
    assert repo.get_cook_state() == CookState({}, {}, {})
    assert repo.use_ingredient(key, AVOCADO, Decimal("35.1")) == 2
    assert repo.use_ingredient(key, SPINACH, None) == 3
    assert repo.get_cook_state().usage == {
        key: {AVOCADO: Decimal("35.1"), SPINACH: None}
    }
    with pytest.raises(sqlite3.IntegrityError):
        repo.use_ingredient(key, AVOCADO, Decimal(1))
    assert repo.unuse_ingredient(key, AVOCADO) == 4
    assert repo.get_cook_state().usage == {key: {SPINACH: None}}


def test_record_and_clear_cook(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    repo.save_settings(SETTINGS)
    batch, today = plan(date(2026, 11, 7), "l5"), date(2026, 11, 9)
    other = extra(today, "l3")
    repo.use_ingredient(batch, AVOCADO, Decimal(10))
    repo.set_dish_portions(batch, 2)
    repo.use_ingredient(other, PASTA, Decimal(90))
    # Ключ партии — Сб, приготовили в Пн.
    assert repo.record_cook(batch, today, {SPINACH: None}) == 5
    state = repo.get_cook_state()
    assert state.cooked == {batch: today}
    assert state.usage[batch] == {AVOCADO: Decimal(10), SPINACH: None}

    assert repo.clear_cook(batch) == 6
    state = repo.get_cook_state()
    assert state.cooked == {}
    assert state.usage == {other: {PASTA: Decimal(90)}}  # другой ключ
    assert state.portions == {batch: 2}  # порции остаются


def test_record_cook_is_atomic(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    repo.save_settings(SETTINGS)
    day = date(2026, 11, 9)
    repo.record_cook(extra(day, "l3"), day, {PASTA: Decimal(90)})
    before = repo.get_cook_state()
    with pytest.raises(sqlite3.IntegrityError):
        # уже отмечен
        repo.record_cook(extra(day, "l3"), day, {AVOCADO: Decimal(35)})
    assert repo.get_cook_state() == before
    stored = repo.get_settings()
    assert stored is not None and stored.version == 2
