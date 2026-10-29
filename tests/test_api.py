import dataclasses
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from golden_scheduling import SCENARIOS
from scenario_settings import settings_of

from meal_planner.api.main import create_app, get_cook_day, get_repository, get_today
from meal_planner.api.schemas import to_app_state_out, to_recipes_out
from meal_planner.core.cookbook import upcoming_cook
from meal_planner.core.cooking import CookState
from meal_planner.core.models import CycleShuffle, Settings, default_settings
from meal_planner.core.planner import recompute
from meal_planner.core.seed import SEED_CATALOG
from meal_planner.core.shopping import format_qty
from meal_planner.db.repository import Repository, StoredSettings

TODAY = date(2026, 11, 9)
DEFAULT_2X = settings_of(next(s for s in SCENARIOS if s["name"] == "default_2x"))


@pytest.fixture
def repo(tmp_path):
    return Repository(tmp_path / "db.sqlite")


def make_client(repo, today: date = TODAY, cook_day: date | None = None) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_today] = lambda: today
    # День готовки (10.5) — по умолчанию тот же: до 7:00 он ещё вчерашний.
    app.dependency_overrides[get_cook_day] = lambda: cook_day or today
    return TestClient(app)


@pytest.fixture
def client(repo, monkeypatch, tmp_path):
    monkeypatch.setenv("MEALPLAN_STATIC_DIR", str(tmp_path / "no-static"))
    return make_client(repo)


def test_state_without_settings_returns_defaults(client, repo):
    r = client.get("/api/state")
    assert r.status_code == 200
    body = r.json()
    assert body["today"] == "2026-11-09"
    assert body["version"] == 1
    assert body["settings"] == {
        "meal_anchor_date": "2026-11-10",
        "cook_count": 2,
        "cook_weekday1": 3,
        "cook_weekday2": 6,
        "shopping_timing": "same",
        "skipped_cook_date": None,
        "daily_kcal_target": None,
        "daily_protein_target": None,
    }
    assert [d["date"] for d in body["schedule"]][:2] == ["2026-11-09", "2026-11-10"]
    assert len(body["schedule"]) == 14
    assert "recipes" not in body  # справочник — GET /api/recipes
    # первый GET сохраняет дефолты (6.4)
    assert repo.get_settings() == StoredSettings(default_settings(TODAY), 1)


def test_anchor_does_not_move_with_today(client, repo):
    first = client.get("/api/state").json()
    next_day = make_client(repo, TODAY + timedelta(days=1)).get("/api/state").json()
    assert next_day["today"] == "2026-11-10"
    assert first["settings"] == next_day["settings"]
    assert next_day["settings"]["meal_anchor_date"] == "2026-11-10"
    assert next_day["version"] == 1


def test_state_with_bad_range_saves_nothing(client, repo):
    r = client.get("/api/state", params={"from": "2026-11-10", "to": "2026-11-09"})
    assert r.status_code == 422
    assert repo.get_settings() is None


def test_state_with_saved_settings_matches_recompute(client, repo):
    repo.save_settings(DEFAULT_2X)
    body = client.get("/api/state").json()
    expected = to_app_state_out(
        recompute(SEED_CATALOG, DEFAULT_2X, TODAY, version=1), SEED_CATALOG
    )
    assert body == expected.model_dump(mode="json")
    assert body["version"] == 1


def test_state_range_keeps_trip_from_today(client, repo):
    repo.save_settings(DEFAULT_2X)
    body = client.get(
        "/api/state", params={"from": "2026-11-20", "to": "2026-11-22"}
    ).json()
    assert [d["date"] for d in body["schedule"]] == [
        "2026-11-20",
        "2026-11-21",
        "2026-11-22",
    ]
    assert body["next_shopping_trip"]["cook_date"] == "2026-11-11"
    assert body["next_shopping_trip"]["window_days"] == 3


def test_ids_resolve_and_qty_display(client, repo):
    repo.save_settings(DEFAULT_2X)
    body = client.get("/api/state").json()
    recipes = client.get("/api/recipes").json()
    for day in body["schedule"]:
        assert {
            day["breakfast_id"],
            day["lunch_id"],
            day["dinner_id"],
        } <= recipes.keys()
    trip = body["next_shopping_trip"]
    for item in trip["cook_items"]:
        assert {item["lunch_id"], item["dinner_id"]} <= recipes.keys()
    items = [i for g in trip["shopping_groups"] for i in g["items"]]
    items += [i for r in recipes.values() for i in r["ingredients"]]
    assert items
    assert all(i["qty_display"] == format_qty(i["qty"]) for i in items)


def test_invalid_date_is_422(client):
    assert client.get("/api/state", params={"from": "not-a-date"}).status_code == 422


def test_static_dir_is_served(repo, monkeypatch, tmp_path):
    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text("<h1>meal planner</h1>", "utf-8")
    monkeypatch.setenv("MEALPLAN_STATIC_DIR", str(static))
    client = make_client(repo)
    assert client.get("/").text == "<h1>meal planner</h1>"
    assert client.get("/api/state").status_code == 200


def test_index_is_revalidated_and_assets_are_immutable(repo, monkeypatch, tmp_path):
    # Иначе браузер «на глаз» держит старый index.html со ссылкой на старый
    # бандл, и после обновления образа UI не меняется.
    static = tmp_path / "dist"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<script src='assets/index-abc.js'>", "utf-8")
    (static / "assets" / "index-abc.js").write_text("console.log(1)", "utf-8")
    monkeypatch.setenv("MEALPLAN_STATIC_DIR", str(static))
    client = make_client(repo)

    index = client.get("/")
    assert index.headers["cache-control"] == "no-cache"
    assert client.get("/index.html").headers["cache-control"] == "no-cache"
    again = client.get("/", headers={"If-None-Match": index.headers["etag"]})
    assert again.status_code == 304
    assert again.headers["cache-control"] == "no-cache"
    asset = client.get("/assets/index-abc.js")
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_no_static_dir_gives_404_at_root(client):
    assert client.get("/").status_code == 404


def test_recipes_endpoint(client):
    r = client.get("/api/recipes")
    assert r.status_code == 200
    body = r.json()
    assert list(body) == list(SEED_CATALOG.recipes)
    assert body == {
        rid: out.model_dump(mode="json")
        for rid, out in to_recipes_out(SEED_CATALOG).items()
    }
    assert len(body) == 13  # 12 из JSX + бенедикт b3 (5.1a)


class ExplodingRepository:
    def get_settings(self) -> StoredSettings | None:
        raise AssertionError("/api/recipes не должен читать настройки")

    def init_settings(self, defaults):
        raise AssertionError("/api/recipes не должен писать настройки")

    def get_catalog(self):
        return SEED_CATALOG


def test_recipes_endpoint_reads_only_catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("MEALPLAN_STATIC_DIR", str(tmp_path / "no-static"))
    client = make_client(ExplodingRepository())
    assert client.get("/api/recipes").status_code == 200


def test_state_and_recipes_come_from_db_catalog(client, repo):
    # 5.3: каталог читается из БД — правка в ней видна в плане и рецептах.
    repo.save_settings(DEFAULT_2X)
    with repo._connect() as conn:
        conn.execute("UPDATE recipes SET name = 'Своя курица' WHERE id = 'l1'")
        conn.execute(
            "UPDATE slot_versions SET recipe_id = 'l2' WHERE slot = 1 AND kind = 'lunch'"
        )
    assert client.get("/api/recipes").json()["l1"]["name"] == "Своя курица"
    state = client.get("/api/state").json()
    lunches = {d["cycle_index"]: d["lunch_id"] for d in state["schedule"]}
    assert lunches[1] == lunches[2] == "l2"


def test_summary_reshapes_state_for_today_and_tomorrow(client, repo):
    repo.save_settings(DEFAULT_2X)
    r = client.get("/api/summary")
    assert r.status_code == 200
    body = r.json()
    state = recompute(
        SEED_CATALOG, DEFAULT_2X, TODAY, TODAY, TODAY + timedelta(days=1), 1
    )
    assert body["today"] == TODAY.isoformat()
    assert body["version"] == 1
    for key, day in zip(("today", "tomorrow"), state.schedule, strict=True):
        assert day.breakfast and day.lunch and day.dinner
        assert body["days"][key] == {
            "date": day.date.isoformat(),
            "breakfast": day.breakfast.name,
            "lunch": day.lunch.name,
            "dinner": day.dinner.name,
            "kcal": day.kcal_total,
            "protein": day.protein_total,
            "is_cook_day": day.is_cook_day,
            "is_day_zero": day.is_day_zero,
            "is_before_start": day.is_before_start,
            "is_skipped": day.is_skipped,
            "kcal_delta": None,
            "protein_delta": None,
            "nutrition": f"≈ {day.kcal_total} ккал · {day.protein_total} г белка",
        }
    assert (body["kcal_target"], body["protein_target"]) == (None, None)

    trip, out = state.next_shopping_trip, body["next_shopping"]
    assert (out["date"], out["cook_date"]) == ("2026-11-11", "2026-11-11")
    assert (out["window_start"], out["window_end"]) == ("2026-11-12", "2026-11-14")
    assert out["cook"] == [
        f"{c.lunch.name} + {c.dinner.name} — x{c.portions}" for c in trip.cook_items
    ]
    # Плоский список в порядке категорий UI, по строке на позицию.
    names = [i.name for g in trip.shopping_groups for i in g.items]
    assert [line.split(" — ")[0] for line in out["items"]] == names
    # Формат — как «Что купить» в UI: с «≈ N шт» и без.
    assert "куриное филе — 480 г" in out["items"]
    assert "авокадо — 200 г (≈ 2 шт)" in out["items"]
    assert "сок лимона — 40 мл (≈ 1 шт)" in out["items"]


def test_summary_before_start_and_day_zero_have_no_dishes(client, repo):
    # Якорь послезавтра: сегодня цикл ещё не начался, завтра — день 0.
    repo.save_settings(
        dataclasses.replace(DEFAULT_2X, meal_anchor_date=TODAY + timedelta(days=2))
    )
    days = client.get("/api/summary").json()["days"]
    for day in days.values():
        assert (day["breakfast"], day["lunch"], day["dinner"]) == (None, None, None)
    assert days["today"]["is_before_start"] and not days["today"]["is_cook_day"]
    assert days["tomorrow"]["is_day_zero"] and days["tomorrow"]["is_cook_day"]


def test_summary_first_request_saves_defaults(client, repo):
    assert repo.get_settings() is None
    assert client.get("/api/summary").status_code == 200
    assert repo.get_settings() == StoredSettings(default_settings(TODAY), 1)


def test_summary_is_compact(client, repo):
    # Атрибуты сенсора HA должны быть короткими; /api/state — ~6 КБ.
    assert len(client.get("/api/summary").content) < 4096


def test_skip_and_unskip_next_cook(client, repo):
    repo.save_settings(DEFAULT_2X)
    body = post(client, "skip-next-cook", None).json()
    assert body["settings"]["skipped_cook_date"] == "2026-11-11"
    assert body["skipped_cook"] == {
        "cook_date": "2026-11-11",
        "pause_start": "2026-11-12",
        "pause_end": "2026-11-14",
        "can_undo": True,
    }
    assert body["can_skip_next_cook"] is False
    assert body["next_shopping_trip"]["cook_date"] == "2026-11-14"
    paused = [d["date"] for d in body["schedule"] if d["is_skipped"]]
    assert paused == ["2026-11-12", "2026-11-13", "2026-11-14"]

    summary = client.get("/api/summary").json()
    assert summary["skipped_cook"] == body["skipped_cook"]

    again = post(client, "skip-next-cook", None)
    assert again.status_code == 422
    assert again.json()["detail"][0]["msg"].startswith("Уже пропущена готовка ср 11.11")

    body = post(client, "unskip-cook", None).json()
    assert body["skipped_cook"] is None
    assert repo.get_settings().settings == DEFAULT_2X
    assert post(client, "unskip-cook", None).status_code == 422


def test_health_reads_db_without_writing(tmp_path, monkeypatch):
    monkeypatch.setenv("MEALPLAN_STATIC_DIR", str(tmp_path / "no-static"))

    class ReadOnlyRepository(ExplodingRepository):
        def get_settings(self):
            return None

        def save_settings(self, settings):
            raise AssertionError("/api/health не должен писать настройки")

    r = make_client(ReadOnlyRepository()).get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def post(client, action, body, **params):
    return client.post(f"/api/actions/{action}", json=body, params=params)


def test_changing_cook_weekday_shifts_cook_date(client, repo):
    # 2.7: today — Пн 2026-11-09; Ср/Сб → ближайшая готовка Ср 11-11,
    # после переноса первого дня на Пн — сегодня.
    repo.save_settings(DEFAULT_2X)
    before = client.get("/api/state").json()["next_shopping_trip"]["cook_date"]
    body = post(
        client, "set-cook-schedule", {"cook_count": 2, "cook_weekday1": 1}
    ).json()
    assert (before, body["next_shopping_trip"]["cook_date"]) == (
        "2026-11-11",
        "2026-11-09",
    )
    assert body["settings"]["cook_weekday2"] == 6  # второй день не тронут


def test_action_response_matches_state_and_bumps_version(client, repo):
    repo.save_settings(DEFAULT_2X)
    first = post(client, "set-shopping-timing", {"shopping_timing": "before"}).json()
    assert first["version"] == 2
    assert first == client.get("/api/state").json()
    second = post(
        client, "set-cook-schedule", {"cook_count": 1, "cook_weekday1": 7}
    ).json()
    assert second["version"] == 3
    assert second == client.get("/api/state").json()


def test_first_action_persists_defaults_plus_change(client, repo):
    body = post(client, "set-shopping-timing", {"shopping_timing": "before"}).json()
    assert body["version"] == 1
    stored = repo.get_settings()
    assert stored.settings.meal_anchor_date == date(2026, 11, 10)  # дефолт: завтра
    assert stored.settings.shopping_timing == "before"


def test_shopping_before_is_day_before_cook(client, repo):
    repo.save_settings(DEFAULT_2X)
    trip = post(client, "set-shopping-timing", {"shopping_timing": "before"}).json()[
        "next_shopping_trip"
    ]
    assert (trip["cook_date"], trip["shopping_date"]) == ("2026-11-11", "2026-11-10")


def test_action_respects_range(client, repo):
    repo.save_settings(DEFAULT_2X)
    body = post(
        client,
        "set-shopping-timing",
        {"shopping_timing": "same"},
        **{"from": "2026-12-01", "to": "2026-12-02"},
    ).json()
    assert [d["date"] for d in body["schedule"]] == ["2026-12-01", "2026-12-02"]


@pytest.mark.parametrize(
    ("action", "body"),
    [
        ("set-cook-schedule", {"cook_count": 3, "cook_weekday1": 1}),
        ("set-cook-schedule", {"cook_count": 2, "cook_weekday1": "x"}),
        ("set-cook-schedule", {}),
        ("set-shopping-timing", {"shopping_timing": "later"}),
        ("set-shopping-timing", {}),
    ],
)
def test_action_type_errors_are_422_and_not_saved(client, repo, action, body):
    assert post(client, action, body).status_code == 422
    assert repo.get_settings() is None


def test_set_anchor_date_matches_recompute(client, repo):
    repo.save_settings(DEFAULT_2X)
    body = post(client, "set-anchor-date", {"meal_anchor_date": "2026-11-12"}).json()
    moved = dataclasses.replace(DEFAULT_2X, meal_anchor_date=date(2026, 11, 12))
    expected = to_app_state_out(
        recompute(SEED_CATALOG, moved, TODAY, version=2), SEED_CATALOG
    )
    assert body == expected.model_dump(mode="json")
    # день 0 — накануне нового якоря, и он единственный в окне
    assert [d["date"] for d in body["schedule"] if d["is_day_zero"]] == ["2026-11-11"]


def test_actions_do_not_overwrite_each_other(client, repo):
    repo.save_settings(DEFAULT_2X)
    post(client, "set-anchor-date", {"meal_anchor_date": "2026-11-12"})
    body = post(
        client, "set-cook-schedule", {"cook_count": 1, "cook_weekday1": 7}
    ).json()
    assert body["settings"]["meal_anchor_date"] == "2026-11-12"
    assert body["settings"]["cook_count"] == 1


@pytest.mark.parametrize(
    "body",
    [
        {"meal_anchor_date": ""},  # в JS очищенное поле даты сохранялось как ""
        {"meal_anchor_date": "2026-13-01"},
        {"meal_anchor_date": "tomorrow"},
        {},
    ],
)
def test_set_anchor_date_rejects_bad_dates(client, repo, body):
    assert post(client, "set-anchor-date", body).status_code == 422
    assert repo.get_settings() is None


def test_set_cycle_position(client, repo):
    # Пн 9 нояб., готовка Ср/Сб: сегодня возможны день 0, блюда 2, 3, 5.
    repo.save_settings(DEFAULT_2X)
    options = client.get("/api/state").json()["today_position"]["options"]
    assert [o["dish"] for o in options] == [0, 2, 3, 5]
    body = post(client, "set-cycle-position", {"dish": 5}).json()
    assert body["today_position"]["current"] == 5
    assert body["schedule"][0]["cycle_index"] == 5
    assert body["settings"]["meal_anchor_date"] == options[-1]["anchor"]
    assert body["version"] == 2


def test_cycle_position_day_zero_starts_tomorrow(client, repo):
    repo.save_settings(DEFAULT_2X)
    body = post(client, "set-cycle-position", {"dish": 0}).json()
    assert body["settings"]["meal_anchor_date"] == "2026-11-10"
    assert body["today_position"]["current"] == 0
    assert body["schedule"][0]["is_day_zero"]


@pytest.mark.parametrize(
    ("body", "msg"),
    [
        (
            {"dish": 1},
            "Сегодня может быть только: день 0, блюдо №2, блюдо №3, блюдо №5",
        ),
        ({"dish": 6}, None),
        ({}, None),
    ],
)
def test_set_cycle_position_rejects_unreachable(client, repo, body, msg):
    r = post(client, "set-cycle-position", body)
    assert r.status_code == 422
    if msg:
        assert r.json()["detail"] == [
            {"type": "value_error", "loc": ["body", "dish"], "msg": msg}
        ]
    assert repo.get_settings() is None


@pytest.mark.parametrize("weekday", [0, 8])
def test_weekday_out_of_range_is_422(client, repo, weekday):
    r = post(client, "set-cook-schedule", {"cook_count": 1, "cook_weekday1": weekday})
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"] == ["body", "cook_weekday1"]
    assert repo.get_settings() is None


def test_second_day_required_for_two_cooks(client, repo):
    # Раньше здесь был 500 из cook_weekdays: второго дня нет ни в запросе,
    # ни в сохранённых настройках.
    single = Settings(date(2026, 11, 5), 1, 7, None, "same")
    repo.save_settings(single)
    r = post(client, "set-cook-schedule", {"cook_count": 2, "cook_weekday1": 3})
    assert r.status_code == 422
    assert r.json()["detail"] == [
        {
            "type": "value_error",
            "loc": ["body", "cook_weekday2"],
            "msg": "При двух готовках в неделю нужен второй день готовки",
        }
    ]
    assert repo.get_settings() == StoredSettings(single, 1)  # ничего не записано
    ok = post(
        client,
        "set-cook-schedule",
        {"cook_count": 2, "cook_weekday1": 3, "cook_weekday2": 5},
    )
    assert ok.status_code == 200
    assert ok.json()["version"] == 2


def test_range_from_after_to_is_422(client):
    r = client.get("/api/state", params={"from": "2026-11-10", "to": "2026-11-09"})
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"] == ["query", "from"]


def test_range_to_before_today_without_from_is_422(client):
    assert client.get("/api/state", params={"to": "2026-11-08"}).status_code == 422


def test_range_limit_366_days(client):
    start = date(2026, 11, 9)
    ok = client.get(
        "/api/state",
        params={"from": str(start), "to": str(start + timedelta(days=365))},
    )
    assert ok.status_code == 200
    assert len(ok.json()["schedule"]) == 366
    too_long = client.get(
        "/api/state",
        params={"from": str(start), "to": str(start + timedelta(days=366))},
    )
    assert too_long.status_code == 422
    assert too_long.json()["detail"][0]["msg"] == "Диапазон дат — не больше 366 дней"


def test_action_with_bad_range_saves_nothing(client, repo):
    r = post(
        client,
        "set-shopping-timing",
        {"shopping_timing": "before"},
        **{"from": "2026-11-10", "to": "2026-11-09"},
    )
    assert r.status_code == 422
    assert repo.get_settings() is None


def test_same_cook_days_are_rejected(client, repo):
    r = post(
        client,
        "set-cook-schedule",
        {"cook_count": 2, "cook_weekday1": 4, "cook_weekday2": 4},
    )
    assert r.status_code == 422
    assert r.json()["detail"] == [
        {
            "type": "value_error",
            "loc": ["body", "cook_weekday2"],
            "msg": "Два дня готовки должны различаться",
        }
    ]
    assert repo.get_settings() is None


BROKEN = Settings(date(2026, 11, 5), 2, 4, 4, "before")  # Чт/Чт из старой версии


def test_invalid_saved_settings_fall_back_with_warning(client, repo, caplog):
    repo.save_settings(BROKEN)
    with caplog.at_level("WARNING", logger="meal_planner.api.main"):
        body = client.get("/api/state").json()
    assert body["settings"] == {
        "meal_anchor_date": "2026-11-05",  # якорь сохранён
        "cook_count": 2,
        "cook_weekday1": 3,
        "cook_weekday2": 6,
        "shopping_timing": "same",
        "skipped_cook_date": None,
        "daily_kcal_target": None,
        "daily_protein_target": None,
    }
    assert body["version"] == 1
    assert "cook_weekday2: Два дня готовки должны различаться" in caplog.text
    assert repo.get_settings().settings == BROKEN  # GET не перезаписывает


def test_action_heals_invalid_saved_settings(client, repo):
    repo.save_settings(BROKEN)
    body = post(client, "set-shopping-timing", {"shopping_timing": "before"}).json()
    stored = repo.get_settings()
    assert stored.version == 2
    assert stored.settings == Settings(date(2026, 11, 5), 2, 3, 6, "before")
    assert body["settings"]["cook_weekday2"] == 6


# --- Каталог (5.4) ---

ING = {i.name: i.id for i in SEED_CATALOG.ingredients.values()}
NEW_LUNCH = {
    "name": "Курица с булгуром",
    "kind": "lunch",
    "ingredients": [
        {"ingredient_id": ING["куриное филе"], "qty": 180},
        {"ingredient_id": ING["булгур"], "qty": 80},
    ],
    "steps": ["Отварите булгур.", "Обжарьте курицу."],
    "storage": {"keep": "До 3 дней", "pan": "Уровень 4", "micro": "2 мин"},
}


def test_save_recipe_creates_and_edits(client, repo):
    repo.save_settings(DEFAULT_2X)
    body = post(client, "save-recipe", NEW_LUNCH).json()
    recipe = body["recipe"]
    # Ккал — сервер: 180 г × 1.20 + 80 г × 3.42 = 489.6 → 490.
    assert (recipe["id"], recipe["status"], recipe["kcal"]) == ("l6", "active", 490)
    assert body["state"]["version"] == 2
    assert client.get("/api/recipes").json()["l6"] == recipe

    edited = post(client, "save-recipe", {**NEW_LUNCH, "id": "l6", "name": "Новое"})
    assert edited.json()["recipe"]["name"] == "Новое"
    assert edited.json()["state"]["version"] == 3


def test_save_recipe_errors_write_nothing(client, repo):
    repo.save_settings(DEFAULT_2X)
    bad = {**NEW_LUNCH, "ingredients": [{"ingredient_id": 999, "qty": 10}]}
    r = post(client, "save-recipe", bad)
    assert r.status_code == 422
    assert r.json()["detail"] == [
        {
            "type": "value_error",
            "loc": ["body", "ingredients", 0, "ingredient_id"],
            "msg": "Такого ингредиента нет в справочнике",
        }
    ]
    assert repo.get_settings().version == 1
    assert "l6" not in repo.get_catalog().recipes


def test_first_catalog_action_saves_defaults(client, repo):
    body = post(client, "save-recipe", NEW_LUNCH).json()
    assert body["state"]["version"] == 2  # дефолты — 1, рецепт — 2
    assert repo.get_settings().settings == default_settings(TODAY)


def test_archive_and_restore(client, repo):
    r = post(client, "archive-recipe", {"id": "d3"})
    assert r.status_code == 422
    assert "единственный ужин позиции №3" in r.json()["detail"][0]["msg"]
    assert repo.get_settings() is None  # невалидное действие ничего не пишет

    archived = post(client, "archive-recipe", {"id": "b3"}).json()
    assert archived["recipe"]["status"] == "archived"
    restored = post(client, "restore-recipe", {"id": "b3"}).json()
    assert restored["recipe"]["status"] == "active"
    assert restored["state"]["version"] == 3


PROPOSED = {
    "name": "Говядина с булгуром и баклажаном",
    "kind": "dinner",
    "ingredients": [
        {"name": "Говядина", "qty": 0.15, "unit": "кг"},
        {"name": "булгур", "qty": 70, "unit": "г"},
        {"name": "баклажан", "qty": 150, "unit": "г"},
    ],
    "steps": ["Отварите булгур.", "Потушите говядину с баклажаном."],
    "storage": {"keep": "До 3 дней", "pan": "Уровень 4", "micro": "2 мин"},
}


def test_draft_propose_confirm_discard(client, repo):
    # 8.6a: черновик от Assist — в справочнике, но не в плане.
    repo.save_settings(DEFAULT_2X)
    plan = client.get("/api/state").json()
    body = post(client, "propose-recipe", PROPOSED).json()
    draft = body["recipe"]
    assert (draft["id"], draft["status"]) == ("d6", "draft")
    assert draft["kcal"] > 0 and draft["protein"] > 0
    assert body["state"]["schedule"] == plan["schedule"]
    assert body["state"]["cycle_slots"] == plan["cycle_slots"]

    confirmed = post(client, "confirm-draft", {"id": "d6"}).json()
    assert confirmed["recipe"]["status"] == "active"
    assert any("d6" in s["dinner_ids"] for s in confirmed["state"]["cycle_slots"])

    # Второй — другой состав: повтор подтверждённого отклонило бы сходство.
    chickpeas = [
        {"name": "нут консервированный", "qty": 150, "unit": "г"},
        {"name": "фета", "qty": 40, "unit": "г"},
    ]
    post(
        client, "propose-recipe", {**PROPOSED, "name": "Ещё", "ingredients": chickpeas}
    )
    discarded = post(client, "discard-draft", {"id": "d7"})
    assert discarded.json()["id"] == "d7"
    assert discarded.json()["state"]["version"] == 5
    assert "d7" not in client.get("/api/recipes").json()
    r = post(client, "discard-draft", {"id": "d6"})
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"] == ["body", "id"]


def test_update_draft_and_similar_drafts(client, repo):
    # 8.6c: черновики между собой на сходство не проверяются; свой черновик
    # правится целиком, остаётся черновиком; «В план» сверяет с пулом.
    repo.save_settings(DEFAULT_2X)
    first = post(client, "propose-recipe", PROPOSED).json()["recipe"]
    second = post(
        client, "propose-recipe", {**PROPOSED, "name": "Говядина с булгуром 2"}
    )
    assert second.status_code == 200  # похож на черновик — можно
    renamed = {**PROPOSED, "id": first["id"], "name": "Говядина с цукини"}
    edited = post(client, "update-draft", renamed).json()
    assert (edited["recipe"]["id"], edited["recipe"]["status"]) == (
        first["id"],
        "draft",
    )
    assert edited["recipe"]["name"] == "Говядина с цукини"
    assert post(client, "update-draft", {**renamed, "id": "d1"}).status_code == 422
    confirmed = post(client, "confirm-draft", {"id": first["id"]})
    assert confirmed.status_code == 200
    # Второй черновик теперь похож на подтверждённый из пула — отказ.
    r = post(client, "confirm-draft", {"id": second.json()["recipe"]["id"]})
    assert r.status_code == 422
    assert "похоже на «Говядина с цукини»" in r.json()["detail"][0]["msg"]


def test_propose_similar_recipe_is_refused(client, repo):
    # 8.6c: похожее на существующее — 422, ничего не пишется.
    repo.save_settings(DEFAULT_2X)
    lines = [
        {"name": "говядина", "qty": 180, "unit": "г"},
        {"name": "гречка", "qty": 80, "unit": "г"},
    ]
    r = post(client, "propose-recipe", {**PROPOSED, "ingredients": lines})
    assert r.status_code == 422
    (error,) = r.json()["detail"]
    assert error["loc"] == ["body", "ingredients"]
    assert error["msg"].startswith("Похоже на «Гречка с тушёной говядиной»")
    assert repo.get_settings().version == 1


def test_propose_unknown_ingredient_writes_nothing(client, repo):
    repo.save_settings(DEFAULT_2X)
    lines = [*PROPOSED["ingredients"], {"name": "тофу", "qty": 100, "unit": "г"}]
    r = post(client, "propose-recipe", {**PROPOSED, "ingredients": lines})
    assert r.status_code == 422
    assert r.json()["detail"] == [
        {
            "type": "value_error",
            "loc": ["body", "ingredients", 3, "name"],
            "msg": "«тофу» нет в справочнике — опишите его в new_ingredients",
        }
    ]
    assert repo.get_settings().version == 1
    assert "d6" not in repo.get_catalog().recipes


def test_propose_with_new_ingredient(client, repo):
    # 8.6b: новый ингредиент — в справочник вместе с черновиком и остаётся.
    repo.save_settings(DEFAULT_2X)
    tofu = {
        "name": "тофу",
        "unit": "г",
        "category": "Белок",
        "kcal_100g": 76,
        "protein_100g": 8.08,
    }
    lines = [*PROPOSED["ingredients"], {"name": "Тофу", "qty": 0.1, "unit": "кг"}]
    body = post(
        client,
        "propose-recipe",
        {**PROPOSED, "ingredients": lines, "new_ingredients": [tofu]},
    ).json()
    line = body["recipe"]["ingredients"][3]
    assert (line["name"], line["qty"], line["from_assist"]) == ("тофу", 100, True)
    assert body["recipe"]["ingredients"][0]["qty"] == 150  # 0.15 кг
    assert body["state"]["version"] == 2  # ингредиент и рецепт — одна запись

    def listed() -> dict:
        catalog = client.get("/api/ingredients").json()["ingredients"]
        return {i["name"]: i for i in catalog}

    tofu_out = listed()["тофу"]
    assert (tofu_out["source"], tofu_out["from_assist"]) == (
        "Assist: оценка модели",
        True,
    )
    post(client, "discard-draft", {"id": body["recipe"]["id"]})
    assert "тофу" in listed()

    bad = {**tofu, "name": "мисо", "unit": "шт"}
    r = post(client, "propose-recipe", {**PROPOSED, "new_ingredients": [bad]})
    assert r.json()["detail"][0]["loc"] == ["body", "new_ingredients", 0, "unit"]
    assert repo.get_settings().version == 3


def test_set_cycle_slot(client, repo):
    # 8.4b: состав — с первого некупленного цикла. Пн 09.11: закупка ср 11.11
    # не куплена — цикл 2, его партия с вс 15.11.
    repo.save_settings(DEFAULT_2X)
    body = post(
        client, "set-cycle-slot", {"slot": 1, "lunch_ids": ["l2"], "dinner_ids": ["d1"]}
    )
    state = body.json()
    assert state["version"] == 2
    assert state["cycle_slots"][0] == {
        "slot": 1,
        "lunch_ids": ["l2"],
        "dinner_ids": ["d1"],
        "changes_from": "2026-11-15",
    }
    for day in state["schedule"]:
        if day["cycle_index"] == 1:
            expected = "l2" if day["date"] >= "2026-11-15" else "l1"
            assert day["lunch_id"] == expected, day["date"]
    r = post(
        client, "set-cycle-slot", {"slot": 1, "lunch_ids": ["d1"], "dinner_ids": ["d1"]}
    )
    assert r.json()["detail"][0]["loc"] == ["body", "lunch_ids", 0]


def test_ingredients_and_save_ingredient(client, repo):
    catalog = client.get("/api/ingredients").json()
    assert catalog["units"] == ["г", "мл", "шт", "ч.л.", "ст.л.", "зубчик"]
    assert catalog["categories"][0] == "Белок"
    listed = catalog["ingredients"]
    assert [i["name"] for i in listed] == list(SEED_CATALOG.ingredients)
    eggs = next(i for i in listed if i["name"] == "яйца")
    assert (eggs["unit"], eggs["grams_per_unit"], eggs["used_in"]) == (
        "шт",
        50,
        ["b1", "b3", "d5"],
    )

    tofu = {
        "name": "тофу",
        "unit": "г",
        "category": "Белок",
        "kcal_100g": 76,
        "protein_100g": 8.08,
    }
    body = post(client, "save-ingredient", tofu).json()
    assert body["ingredient"]["id"] == len(listed) + 1
    assert body["ingredient"]["used_in"] == []
    assert body["state"]["version"] == 2

    r = post(client, "save-ingredient", {**eggs, "unit": "г"})
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"] == ["body", "unit"]


def test_state_marks_recipes_in_plan(client, repo):
    repo.save_settings(DEFAULT_2X)
    plan_use = client.get("/api/state").json()["recipe_plan_use"]
    assert plan_use == dict(recompute(SEED_CATALOG, DEFAULT_2X, TODAY).plan_use)
    assert set(plan_use.values()) == {"bought", "next_trip"}


def test_recipe_preview_is_read_only(client, repo):
    lines = [
        {"ingredient_id": ING["куриное филе"], "qty": 180},
        {"ingredient_id": ING["булгур"], "qty": 0},
    ]
    r = client.post("/api/recipes/preview", json={"ingredients": lines})
    assert r.json() == {"kcal": 215, "protein": 41}  # 180 г × 1.20 = 216 → 215
    assert repo.get_settings() is None


def test_state_lists_cycle_slots(client, repo):
    slots = client.get("/api/state").json()["cycle_slots"]
    assert slots == [
        {
            "slot": k,
            "lunch_ids": [f"l{k}"],
            "dinner_ids": [f"d{k}"],
            "changes_from": None,
        }
        for k in range(1, 6)
    ]


def test_new_lunch_is_placed_and_archived_with_alternative(client, repo):
    # 8.4b: новый обед сам встаёт в позицию (с меньшим номером при равенстве),
    # рецепт и состав пишутся одной записью; с вариантом его можно в архив.
    repo.save_settings(DEFAULT_2X)
    body = post(client, "save-recipe", NEW_LUNCH).json()
    assert body["state"]["version"] == 2
    assert body["state"]["cycle_slots"][0]["lunch_ids"] == ["l1", "l6"]
    assert body["state"]["cycle_slots"][0]["changes_from"] == "2026-11-15"
    assert repo.get_catalog().slot_at(1, 3).lunch_id == "l6"
    archived = post(client, "archive-recipe", {"id": "l6"}).json()
    assert archived["recipe"]["status"] == "archived"
    assert archived["state"]["cycle_slots"][0]["lunch_ids"] == ["l1"]


def test_empty_nutrition_is_a_field_error(client, repo):
    body = {"name": "тофу", "unit": "г", "category": "Белок"}
    r = post(
        client, "save-ingredient", {**body, "kcal_100g": None, "protein_100g": None}
    )
    assert [(e["loc"], e["msg"]) for e in r.json()["detail"]] == [
        (["body", "kcal_100g"], "Укажите ккал на 100 г"),
        (["body", "protein_100g"], "Укажите белок на 100 г"),
    ]


def test_set_nutrition_target_shows_deviation(client, repo):
    repo.save_settings(DEFAULT_2X)
    body = post(
        client,
        "set-nutrition-target",
        {"daily_kcal_target": 2000, "daily_protein_target": 100},
    ).json()
    assert body["version"] == 2
    assert body["settings"]["daily_kcal_target"] == 2000
    assert body["settings"]["daily_protein_target"] == 100
    for day in body["schedule"]:
        if day["cycle_index"] is None:
            assert (day["kcal_delta"], day["protein_delta"]) == (None, None)
        else:
            assert day["kcal_delta"] == day["kcal_total"] - 2000
            assert day["protein_delta"] == day["protein_total"] - 100
    # null снимает цель, второе поле — независимо
    body = post(
        client,
        "set-nutrition-target",
        {"daily_kcal_target": None, "daily_protein_target": 100},
    ).json()
    assert body["settings"]["daily_kcal_target"] is None
    assert {d["kcal_delta"] for d in body["schedule"]} == {None}
    assert repo.get_settings().settings == dataclasses.replace(
        DEFAULT_2X, daily_protein_target=100
    )


@pytest.mark.parametrize(
    "body",
    [
        {"daily_kcal_target": 20000, "daily_protein_target": None},
        {"daily_kcal_target": None, "daily_protein_target": 5},
        {"daily_kcal_target": 2000},  # оба поля обязательны
    ],
)
def test_nutrition_target_out_of_range_is_422_and_not_saved(client, repo, body):
    repo.save_settings(DEFAULT_2X)
    assert post(client, "set-nutrition-target", body).status_code == 422
    assert repo.get_settings() == StoredSettings(DEFAULT_2X, 1)


def test_summary_shows_deviation_from_target(client, repo):
    repo.save_settings(
        dataclasses.replace(
            DEFAULT_2X, daily_kcal_target=2000, daily_protein_target=100
        )
    )
    body = client.get("/api/summary").json()
    assert (body["kcal_target"], body["protein_target"]) == (2000, 100)
    today = body["days"]["today"]
    kcal_delta, protein_delta = today["kcal"] - 2000, today["protein"] - 100
    assert (today["kcal_delta"], today["protein_delta"]) == (kcal_delta, protein_delta)

    def signed(n):
        return "±0" if n == 0 else f"+{n}" if n > 0 else f"−{-n}"

    assert today["nutrition"] == (
        f"≈ {today['kcal']} ккал ({signed(kcal_delta)})"
        f" · {today['protein']} г белка ({signed(protein_delta)})"
    )


def test_shopping_remaining_subtracts_stock_read_only(client, repo):
    repo.save_settings(DEFAULT_2X)
    before = client.get("/api/state").json()
    summary = client.get("/api/summary").json()["next_shopping"]
    stock = [
        {"ingredient_id": 6, "qty": 1, "unit": "шт"},  # авокадо, 130 г
        {"ingredient_id": 10, "qty": 1, "unit": "шт"},  # брокколи: нет веса штуки
        {"ingredient_id": 999, "qty": 1, "unit": "шт"},
    ]
    r = client.post("/api/shopping/remaining", json={"stock": stock})
    assert r.status_code == 200
    body = r.json()
    # Та же закупка, что в summary, строки в том же формате.
    for key in ("date", "cook_date", "window_start", "window_end", "cook"):
        assert body[key] == summary[key]
    assert "авокадо — 200 г (≈ 2 шт)" in summary["items"]
    assert "авокадо — 70 г (≈ 1 шт)" in body["items"]
    assert [i for i in body["items"] if not i.startswith("авокадо")] == [
        i for i in summary["items"] if not i.startswith("авокадо")
    ]
    assert [(i["ingredient_id"], i["name"]) for i in body["ignored"]] == [
        (10, "брокколи"),
        (999, None),
    ]
    # Только чтение: версия и план — прежние, «Закупки» — полный список.
    assert client.get("/api/state").json() == before
    assert repo.get_settings() == StoredSettings(DEFAULT_2X, 1)


def test_shopping_remaining_first_request_saves_defaults(client, repo):
    assert client.post("/api/shopping/remaining", json={"stock": []}).status_code == 200
    assert repo.get_settings() == StoredSettings(default_settings(TODAY), 1)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"stock": [{"ingredient_id": "яйца", "qty": 1, "unit": "шт"}]},
        {"stock": [{"ingredient_id": 1, "qty": "много", "unit": "шт"}]},
        {"stock": [{"ingredient_id": 1, "qty": 1}]},
    ],
)
def test_shopping_remaining_bad_body_is_422(client, body):
    assert client.post("/api/shopping/remaining", json=body).status_code == 422


def test_summary_lists_cooking(client, repo):
    repo.save_settings(DEFAULT_2X)  # сегодня Пн 09.11, партия Сб 07.11
    cooking = client.get("/api/summary").json()["cooking"]
    assert cooking["cook_date"] == "2026-11-07"
    assert [d["portions"] for d in cooking["dishes"]] == [1] * 8
    assert not any(d["cooked"] for d in cooking["dishes"])
    breakfast = cooking["breakfast"]
    assert (breakfast["date"], breakfast["cooked"]) == ("2026-11-09", False)
    assert breakfast["name"] == SEED_CATALOG.recipes[breakfast["recipe_id"]].name


def test_cookbook_lists_upcoming_cook_and_recipes(client, repo):
    # 9.1: сегодня Пн 09.11, готовка Ср/Сб — ближайшая Ср 11.11.
    repo.save_settings(DEFAULT_2X)
    book = client.get("/api/cookbook").json()
    assert book["today"] == "2026-11-09"
    cook = book["cook"]
    assert (cook["date"], cook["is_today"]) == ("2026-11-11", False)
    expected = upcoming_cook(SEED_CATALOG, DEFAULT_2X, TODAY).dishes
    assert [(d["recipe_id"], d["planned_portions"]) for d in cook["dishes"]] == [
        (d.recipe.id, d.planned_portions) for d in expected
    ]
    assert all(d["scale"] == 1 for d in cook["dishes"])
    assert [r["id"] for r in book["lunch"]] == [f"l{i}" for i in range(1, 6)]
    assert [r["id"] for r in book["dinner"]] == [f"d{i}" for i in range(1, 6)]
    assert [r["id"] for r in book["breakfast"]] == ["b1", "b2", "b3"]
    l1, b1 = book["lunch"][0], book["breakfast"][0]
    seed_l1 = SEED_CATALOG.recipes["l1"]
    assert l1["steps"] == list(seed_l1.steps)
    assert l1["storage"]["keep"] == seed_l1.storage.keep
    assert b1["storage"] is None
    ingredients = [i for r in book["lunch"] for i in r["ingredients"]]
    ingredients += [i for d in cook["dishes"] for i in d["ingredients"]]
    assert all(set(i) == {"name", "qty_display", "unit"} for i in ingredients)
    assert repo.get_settings().version == 1  # только чтение


def test_cookbook_on_cook_day(repo):
    repo.save_settings(DEFAULT_2X)
    cook = make_client(repo, date(2026, 11, 11)).get("/api/cookbook").json()["cook"]
    assert (cook["date"], cook["is_today"]) == ("2026-11-11", True)


def test_cookbook_first_request_saves_defaults(client, repo):
    assert client.get("/api/cookbook").status_code == 200
    assert repo.get_settings() == StoredSettings(default_settings(TODAY), 1)


def test_cookbook_hides_drafts_and_archive(client, repo):
    repo.save_settings(DEFAULT_2X)
    assert post(client, "propose-recipe", PROPOSED).json()["recipe"]["id"] == "d6"
    post(client, "archive-recipe", {"id": "b3"})
    book = client.get("/api/cookbook").json()
    assert "d6" not in [r["id"] for r in book["dinner"]]
    assert [r["id"] for r in book["breakfast"]] == ["b1", "b2"]


def test_cookbook_dishes_have_target_portion(client, repo):
    # 8.5: масштаб с чт 12.11 — партия Ср 11.11 уже под цель, группы — ×1.
    repo.save_settings(DEFAULT_2X)
    post(
        client,
        "set-nutrition-target",
        {"daily_kcal_target": 2700, "daily_protein_target": None},
    )
    post(client, "set-portion-scaling", {"enabled": True})
    book = client.get("/api/cookbook").json()
    groups = {r["id"]: r for kind in ("lunch", "dinner") for r in book[kind]}
    core = upcoming_cook(repo.get_catalog(), repo.get_settings().settings, TODAY)
    assert len(book["cook"]["dishes"]) == len(core.dishes)
    for dish, expected in zip(book["cook"]["dishes"], core.dishes, strict=True):
        assert dish["scale"] == expected.recipe.scale != 1
        assert dish["kcal"] == expected.recipe.kcal != groups[dish["recipe_id"]]["kcal"]
        assert [i["qty_display"] for i in dish["ingredients"]] == [
            format_qty(i.qty) for i in expected.recipe.ingredients
        ]
        assert dish["ingredients"] != groups[dish["recipe_id"]]["ingredients"]


def test_mark_and_unmark_cooked(client, repo):
    repo.save_settings(DEFAULT_2X)
    dish = client.get("/api/summary").json()["cooking"]["dishes"][0]
    recipe = SEED_CATALOG.recipes[dish["recipe_id"]]
    first = recipe.ingredients[0]
    ingredient = SEED_CATALOG.ingredients[first.name]
    stock = [
        {
            "ingredient_id": ingredient.id,
            "qty": 10000,
            "unit": ingredient.unit,
            "entity_id": "input_number.x",
        }
    ]
    body = {"recipe_id": recipe.id, "stock": stock}

    r = client.post("/api/actions/mark-cooked", json=body)
    assert r.status_code == 200
    out = r.json()
    assert (out["day"], out["recipe_id"], out["version"]) == (
        "2026-11-07",
        recipe.id,
        2,
    )
    assert out["counters"] == [
        {"entity_id": "input_number.x", "value": 10000 - first.qty * dish["portions"]}
    ]
    assert len(out["untracked"]) == len(recipe.ingredients) - 1
    assert repo.get_cook_state().cooked == {(date(2026, 11, 7), recipe.id): TODAY}
    assert client.get("/api/summary").json()["cooking"]["dishes"][0]["cooked"]

    # Повтор — 422, ничего не записано: дважды не списать.
    r = client.post("/api/actions/mark-cooked", json=body)
    assert r.status_code == 422
    assert r.json()["detail"][0]["msg"] == "Блюдо уже отмечено как приготовленное"
    assert repo.get_settings().version == 2

    r = client.post("/api/actions/unmark-cooked", json=body)
    assert r.status_code == 200
    assert r.json()["counters"] == [
        {"entity_id": "input_number.x", "value": 10000 + first.qty * dish["portions"]}
    ]
    assert repo.get_cook_state().cooked == {}


@pytest.mark.parametrize(
    ("action", "recipe_id", "msg"),
    [
        ("mark-cooked", "нет", "Это блюдо сейчас не готовится"),
        ("unmark-cooked", "l1", "Блюдо не отмечено как приготовленное"),
    ],
)
def test_cooked_errors_write_nothing(client, repo, action, recipe_id, msg):
    repo.save_settings(DEFAULT_2X)
    r = client.post(
        f"/api/actions/{action}", json={"recipe_id": recipe_id, "stock": []}
    )
    assert r.status_code == 422
    assert r.json()["detail"] == [
        {"type": "value_error", "loc": ["body", "recipe_id"], "msg": msg}
    ]
    assert repo.get_cook_state().cooked == {}
    assert repo.get_settings().version == 1


def test_mark_cooked_refused_when_stock_is_short(client, repo):
    repo.save_settings(DEFAULT_2X)
    dish = client.get("/api/summary").json()["cooking"]["dishes"][0]
    recipe = SEED_CATALOG.recipes[dish["recipe_id"]]
    first = recipe.ingredients[0]
    ingredient = SEED_CATALOG.ingredients[first.name]
    short = first.qty * dish["portions"] / 2
    stock = [
        {
            "ingredient_id": ingredient.id,
            "qty": short,
            "unit": ingredient.unit,
            "entity_id": "input_number.x",
        }
    ]
    r = client.post(
        "/api/actions/mark-cooked", json={"recipe_id": recipe.id, "stock": stock}
    )
    assert r.status_code == 422
    (error,) = r.json()["detail"]
    assert error["loc"] == ["body", "stock"]
    assert error["msg"].startswith(f"Не хватает продуктов: {first.name} — нужно")
    # Ничего не отмечено и не записано.
    assert repo.get_cook_state().cooked == {}
    assert repo.get_settings().version == 1


def test_dish_portions_change_only_the_write_off(client, repo):
    repo.save_settings(DEFAULT_2X)
    state_before = client.get("/api/state").json()
    dish = client.get("/api/summary").json()["cooking"]["dishes"][0]
    recipe = SEED_CATALOG.recipes[dish["recipe_id"]]
    r = client.post(
        "/api/actions/set-dish-portions", json={"recipe_id": recipe.id, "portions": 3}
    )
    assert r.status_code == 200
    assert r.json() == {
        "version": 2,
        "day": "2026-11-07",
        "recipe_id": recipe.id,
        "portions": 3,
    }
    dish = client.get("/api/summary").json()["cooking"]["dishes"][0]
    assert (dish["portions"], dish["planned_portions"]) == (3, 1)
    # План не меняется — только версия.
    state = client.get("/api/state").json()
    assert {**state, "version": 1} == state_before

    first = recipe.ingredients[0]
    ingredient = SEED_CATALOG.ingredients[first.name]
    stock = [
        {
            "ingredient_id": ingredient.id,
            "qty": 10000,
            "unit": ingredient.unit,
            "entity_id": "input_number.x",
        }
    ]
    body = {"recipe_id": recipe.id, "stock": stock}
    out = client.post("/api/actions/mark-cooked", json=body).json()
    assert out["counters"][0]["value"] == 10000 - first.qty * 3
    # У отмеченного порции не меняются; снятие возвращает те же 3 порции.
    r = client.post(
        "/api/actions/set-dish-portions", json={"recipe_id": recipe.id, "portions": 1}
    )
    assert r.status_code == 422
    out = client.post("/api/actions/unmark-cooked", json=body).json()
    assert out["counters"][0]["value"] == 10000 + first.qty * 3


@pytest.mark.parametrize("portions", [0, 21])
def test_dish_portions_out_of_range_is_422(client, repo, portions):
    repo.save_settings(DEFAULT_2X)
    r = client.post(
        "/api/actions/set-dish-portions", json={"recipe_id": "l1", "portions": portions}
    )
    assert r.status_code == 422
    assert repo.get_dish_portions() == {}


# --- готовка по ингредиентам (10.3) ---

L3 = SEED_CATALOG.recipes["l3"]  # не в партии Сб 07.11 — вне очереди, сегодня
PASTA = SEED_CATALOG.ingredients["паста цельнозерновая"].id  # 90 г в l3


def stock_of(name, qty, entity="input_number.x"):
    i = SEED_CATALOG.ingredients[name]
    return {"ingredient_id": i.id, "qty": qty, "unit": i.unit, "entity_id": entity}


def first_dish(client):
    dish = client.get("/api/summary").json()["cooking"]["dishes"][0]
    return SEED_CATALOG.recipes[dish["recipe_id"]]


def test_use_and_unuse_ingredient(client, repo):
    repo.save_settings(DEFAULT_2X)
    body = {
        "recipe_id": "l3",
        "ingredient_id": PASTA,
        "stock": [stock_of("паста цельнозерновая", 50)],
    }
    r = client.post("/api/actions/use-ingredient", json=body)
    assert r.status_code == 200
    out = r.json()
    assert (out["day"], out["recipe_id"], out["version"]) == ("2026-11-09", "l3", 2)
    # Нужно 90 г, есть 50 — без подтверждения, до 0.
    assert out["counters"] == [{"entity_id": "input_number.x", "value": 0.0}]
    assert repo.get_cook_state().usage == {(TODAY, "l3"): {PASTA: Decimal(50)}}

    r = client.post("/api/actions/use-ingredient", json=body)
    assert r.status_code == 422
    assert r.json()["detail"][0]["msg"] == "Ингредиент уже использован"
    assert repo.get_settings().version == 2  # отказ ничего не пишет

    body["stock"] = [stock_of("паста цельнозерновая", 0)]
    out = client.post("/api/actions/unuse-ingredient", json=body).json()
    # Возвращается списанное — 50 г, а не 90 по рецепту.
    assert out["counters"] == [{"entity_id": "input_number.x", "value": 50.0}]
    assert repo.get_cook_state().usage == {}


def test_cook_recipe_shortage_matches_the_cookbook(client, repo):
    repo.save_settings(DEFAULT_2X)
    stock = [
        stock_of("паста цельнозерновая", 50, "input_number.pasta"),
        stock_of("авокадо", 1000, "input_number.avocado"),
    ]
    preview = client.post("/api/cookbook", json={"stock": stock}).json()
    missing = preview["cooking"]["l3"]["missing"]
    assert missing == ["паста цельнозерновая — нужно 90 г, есть 50 г"]

    body = {"recipe_id": "l3", "stock": stock}
    r = client.post("/api/actions/cook-recipe", json=body)
    assert r.status_code == 422
    assert r.json()["detail"] == [
        {
            "type": "value_error",
            "loc": ["body", "stock"],
            "msg": "Не хватает продуктов: " + "; ".join(missing),
        }
    ]
    assert repo.get_cook_state().cooked == {}

    out = client.post(
        "/api/actions/cook-recipe", json={**body, "allow_missing": True}
    ).json()
    assert out["day"] == "2026-11-09"
    assert out["counters"] == [
        {"entity_id": "input_number.pasta", "value": 0.0},
        {"entity_id": "input_number.avocado", "value": 965.0},
    ]
    assert repo.get_cook_state().cooked == {(TODAY, "l3"): TODAY}
    cooked = client.post("/api/cookbook", json={"stock": stock}).json()["cooking"]["l3"]
    assert (cooked["cooked"], cooked["cooked_today"], cooked["locked"]) == (
        True,
        True,
        True,
    )
    assert cooked["missing"] == []


def test_uncook_returns_used_and_cooked(client, repo):
    repo.save_settings(DEFAULT_2X)
    use = {
        "recipe_id": "l3",
        "ingredient_id": PASTA,
        "stock": [stock_of("паста цельнозерновая", 500, "input_number.pasta")],
    }
    client.post("/api/actions/use-ingredient", json=use)
    stock = [
        stock_of("паста цельнозерновая", 410, "input_number.pasta"),
        stock_of("авокадо", 100, "input_number.avocado"),
    ]
    out = client.post(
        "/api/actions/cook-recipe", json={"recipe_id": "l3", "stock": stock}
    ).json()
    # Пасту списали касанием — второй раз не списывается.
    assert out["counters"] == [{"entity_id": "input_number.avocado", "value": 65.0}]

    stock[1]["qty"] = 65
    out = client.post(
        "/api/actions/uncook-recipe", json={"recipe_id": "l3", "stock": stock}
    ).json()
    assert {c["entity_id"]: c["value"] for c in out["counters"]} == {
        "input_number.pasta": 500.0,
        "input_number.avocado": 100.0,
    }
    assert repo.get_cook_state() == CookState({}, {}, {})


def test_portions_until_the_first_write_off(client, repo):
    repo.save_settings(DEFAULT_2X)
    r = client.post(
        "/api/actions/set-recipe-portions", json={"recipe_id": "l3", "portions": 2}
    )
    assert r.json() == {
        "version": 2,
        "day": "2026-11-09",
        "recipe_id": "l3",
        "portions": 2,
    }
    use = {
        "recipe_id": "l3",
        "ingredient_id": PASTA,
        "stock": [stock_of("паста цельнозерновая", 500)],
    }
    out = client.post("/api/actions/use-ingredient", json=use).json()
    assert out["counters"] == [{"entity_id": "input_number.x", "value": 320.0}]
    locked = "Продукты уже списаны — порции меняются до первого списания"
    r = client.post(
        "/api/actions/set-recipe-portions", json={"recipe_id": "l3", "portions": 1}
    )
    assert (r.status_code, r.json()["detail"][0]["msg"]) == (422, locked)

    # «Готовка» (8.3b): теперь блокирует и первое списание, а не только отметка.
    dish = first_dish(client)
    first = dish.ingredients[0]
    client.post(
        "/api/actions/use-ingredient",
        json={
            "recipe_id": dish.id,
            "ingredient_id": SEED_CATALOG.ingredients[first.name].id,
            "stock": [],
        },
    )
    r = client.post(
        "/api/actions/set-dish-portions", json={"recipe_id": dish.id, "portions": 3}
    )
    assert (r.status_code, r.json()["detail"][0]["msg"]) == (422, locked)


def test_marks_are_shared_with_the_cooking_tab(client, repo):
    repo.save_settings(DEFAULT_2X)
    dish = first_dish(client)
    out = client.post(
        "/api/actions/cook-recipe", json={"recipe_id": dish.id, "stock": []}
    ).json()
    assert out["day"] == "2026-11-07"  # ключ партии
    assert client.get("/api/summary").json()["cooking"]["dishes"][0]["cooked"]
    # Ключ — Сб, приготовили сегодня: на «Рецептах» серый.
    assert client.get("/api/cookbook").json()["cooking"][dish.id]["cooked_today"]
    r = client.post(
        "/api/actions/unmark-cooked", json={"recipe_id": dish.id, "stock": []}
    )
    assert r.status_code == 200
    assert not client.get("/api/summary").json()["cooking"]["dishes"][0]["cooked"]

    breakfast = client.get("/api/summary").json()["cooking"]["breakfast"]["recipe_id"]
    client.post("/api/actions/mark-cooked", json={"recipe_id": breakfast, "stock": []})
    assert client.get("/api/cookbook").json()["cooking"][breakfast]["cooked_today"]


def test_unmark_returns_what_was_written_off(client, repo):
    # Изменение 8.3b (10.3): снятие отметки возвращает списанное по журналу.
    repo.save_settings(DEFAULT_2X)
    dish = first_dish(client)
    first = dish.ingredients[0]
    half = first.qty / 2
    # Половина остатка — в счётчике без сущности: проверка нехватки её
    # учитывает, но списать из него нельзя.
    stock = [
        stock_of(first.name, half),
        {**stock_of(first.name, half), "entity_id": None},
    ]
    body = {"recipe_id": dish.id, "stock": stock}
    out = client.post("/api/actions/mark-cooked", json=body).json()
    assert out["counters"] == [{"entity_id": "input_number.x", "value": 0.0}]
    stock[0]["qty"] = 0
    out = client.post("/api/actions/unmark-cooked", json=body).json()
    assert out["counters"] == [{"entity_id": "input_number.x", "value": half}]


def test_cookbook_has_cooking_for_every_recipe(client, repo):
    repo.save_settings(DEFAULT_2X)
    book = client.get("/api/cookbook").json()
    ids = {r["id"] for kind in ("lunch", "dinner", "breakfast") for r in book[kind]}
    ids |= {d["recipe_id"] for d in book["cook"]["dishes"]}
    assert ids <= set(book["cooking"])
    # Без остатков нехватка не известна.
    assert not any(
        i["short"] or i["have"] is not None
        for c in book["cooking"].values()
        for i in c["ingredients"]
    )
    assert client.post("/api/cookbook", json={"stock": []}).json() == book
    l3 = book["cooking"]["l3"]
    assert (l3["day"], l3["portions"], l3["planned_portions"], l3["locked"]) == (
        "2026-11-09",
        1,
        1,
        False,
    )
    assert [i["need"] for i in l3["ingredients"]] == [
        format_qty(i.qty) for i in L3.ingredients
    ]

    stock = [stock_of("паста цельнозерновая", 50)]
    l3 = client.post("/api/cookbook", json={"stock": stock}).json()["cooking"]["l3"]
    pasta = next(i for i in l3["ingredients"] if i["ingredient_id"] == PASTA)
    assert (pasta["need"], pasta["have"], pasta["short"]) == ("90", "50", True)
    assert sum(i["short"] for i in l3["ingredients"]) == 1


def test_out_of_turn_resets_at_seven_not_midnight(repo):
    # 10.5: Вт 10.11 в 6:30 — день готовки ещё Пн 09.11.
    repo.save_settings(DEFAULT_2X)
    night = make_client(repo, today=TODAY + timedelta(days=1), cook_day=TODAY)
    out = night.post(
        "/api/actions/cook-recipe", json={"recipe_id": "l3", "stock": []}
    ).json()
    assert out["day"] == "2026-11-09"
    book = night.get("/api/cookbook").json()["cooking"]["l3"]
    assert (book["cooked"], book["cooked_today"]) == (True, True)
    r = night.post("/api/actions/cook-recipe", json={"recipe_id": "l3", "stock": []})
    assert r.status_code == 422  # до 7:00 — ещё приготовлено

    # С 7:00 — новый день: снова можно готовить вне очереди.
    morning = make_client(repo, today=TODAY + timedelta(days=1))
    book = morning.get("/api/cookbook").json()["cooking"]["l3"]
    assert (book["day"], book["cooked"], book["cooked_today"]) == (
        "2026-11-10",
        False,
        False,
    )
    out = morning.post(
        "/api/actions/cook-recipe", json={"recipe_id": "l3", "stock": []}
    ).json()
    assert out["day"] == "2026-11-10"


def test_reshuffle_cycle_saves_seed_from_first_free_cycle(client, repo, monkeypatch):
    # 8.4a: пн 09.11, закупка ср 11.11 не куплена — новый порядок с цикла 2,
    # его партия начинается вс 15.11. Зерно — от сервера.
    monkeypatch.setattr("meal_planner.api.main.secrets.randbelow", lambda _: 42)
    repo.save_settings(DEFAULT_2X)
    before = client.get("/api/state").json()
    assert before["cycle_order"] == {"shuffled": False, "changes_from": None}
    body = post(client, "reshuffle-cycle", None).json()
    assert body["version"] == 2
    assert body["cycle_order"] == {"shuffled": True, "changes_from": "2026-11-15"}
    assert "cycle_shuffles" not in body["settings"]  # строки наружу не отдаются
    saved = repo.get_settings().settings
    assert saved.cycle_shuffles == (CycleShuffle(2, 42),)
    # До 15.11 и ближайшая закупка — как были.
    assert body["schedule"][:6] == before["schedule"][:6]
    assert body["next_shopping_trip"] == before["next_shopping_trip"]
    assert body == client.get("/api/state").json()


def test_reset_cycle_order(client, repo):
    repo.save_settings(DEFAULT_2X)
    r = post(client, "reset-cycle-order", None)
    assert r.status_code == 422
    assert r.json()["detail"][0]["msg"] == "Порядок и так по слотам 1→5"
    assert repo.get_settings().version == 1  # ничего не записано
    post(client, "reshuffle-cycle", None)
    body = post(client, "reset-cycle-order", None).json()
    assert body["cycle_order"] == {"shuffled": False, "changes_from": "2026-11-15"}
    assert repo.get_settings().settings.cycle_shuffles[-1] == CycleShuffle(2, None)


def test_set_portion_scaling(client, repo):
    # 8.5: без цели — 422; с целью порции подгоняются с первой некупленной
    # партии (пн 09.11: с чт 12.11), до неё — как в рецептах.
    repo.save_settings(DEFAULT_2X)
    r = post(client, "set-portion-scaling", {"enabled": True})
    assert r.status_code == 422
    assert r.json()["detail"][0]["msg"] == "Сначала задайте цель по ккал"
    assert repo.get_settings().version == 1
    post(
        client,
        "set-nutrition-target",
        {"daily_kcal_target": 2700, "daily_protein_target": None},
    )
    body = post(client, "set-portion-scaling", {"enabled": True}).json()
    assert body["portion_scaling"] == {
        "enabled": True,
        "kcal_target": 2700,
        "changes_from": "2026-11-12",
    }
    for day in body["schedule"]:
        if day["lunch_id"] is None:
            continue
        scaled = day["date"] >= "2026-11-12"
        assert (day["lunch_scale"] != 1) == scaled, day["date"]
    keys = {(r["recipe_id"], r["scale"]) for r in body["scaled_recipes"]}
    for day in body["schedule"]:
        if day["lunch_id"] and day["lunch_scale"] != 1:
            assert (day["lunch_id"], day["lunch_scale"]) in keys
    l1 = next(r for r in body["scaled_recipes"] if r["recipe_id"] == "l1")
    assert l1["scale"] == 1.45
    chicken = next(i for i in l1["ingredients"] if i["name"] == "куриное филе")
    assert chicken["qty_display"] == format_qty(chicken["qty"])
    off = post(client, "set-portion-scaling", {"enabled": False}).json()
    assert off["portion_scaling"]["enabled"] is False
    assert off["scaled_recipes"] == []
