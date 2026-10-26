import dataclasses
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from golden_scheduling import SCENARIOS
from scenario_settings import settings_of

from meal_planner.api.main import create_app, get_repository, get_today
from meal_planner.api.schemas import to_app_state_out, to_recipes_out
from meal_planner.core.models import Settings, default_settings
from meal_planner.core.planner import recompute
from meal_planner.core.recipes_data import ALL_RECIPES
from meal_planner.core.shopping import format_qty
from meal_planner.db.repository import SettingsRepository, StoredSettings

TODAY = date(2026, 11, 9)
DEFAULT_2X = settings_of(next(s for s in SCENARIOS if s["name"] == "default_2x"))


@pytest.fixture
def repo(tmp_path):
    return SettingsRepository(tmp_path / "db.sqlite")


def make_client(repo, today: date = TODAY) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_today] = lambda: today
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
    expected = to_app_state_out(recompute(DEFAULT_2X, TODAY, version=1))
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


def test_no_static_dir_gives_404_at_root(client):
    assert client.get("/").status_code == 404


def test_recipes_endpoint(client):
    r = client.get("/api/recipes")
    assert r.status_code == 200
    body = r.json()
    assert list(body) == list(ALL_RECIPES)
    assert body == {
        rid: out.model_dump(mode="json")
        for rid, out in to_recipes_out(ALL_RECIPES).items()
    }
    assert len(body) == 12


class ExplodingRepository:
    def get_settings(self):
        raise AssertionError("/api/recipes не должен читать настройки")

    def init_settings(self, defaults):
        raise AssertionError("/api/recipes не должен писать настройки")


def test_recipes_endpoint_does_not_touch_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MEALPLAN_STATIC_DIR", str(tmp_path / "no-static"))
    client = make_client(ExplodingRepository())
    assert client.get("/api/recipes").status_code == 200


def test_summary_reshapes_state_for_today_and_tomorrow(client, repo):
    repo.save_settings(DEFAULT_2X)
    r = client.get("/api/summary")
    assert r.status_code == 200
    body = r.json()
    state = recompute(DEFAULT_2X, TODAY, TODAY, TODAY + timedelta(days=1), 1)
    assert body["today"] == TODAY.isoformat()
    assert body["version"] == 1
    for key, day in zip(("today", "tomorrow"), state.schedule, strict=True):
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
        }

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
    expected = to_app_state_out(recompute(moved, TODAY, version=2))
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
