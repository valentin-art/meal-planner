from datetime import date

import pytest
from fastapi.testclient import TestClient
from golden_scheduling import SCENARIOS
from scenario_settings import settings_of

from meal_planner.api.main import create_app, get_repository, get_today
from meal_planner.api.schemas import to_app_state_out
from meal_planner.core.planner import recompute
from meal_planner.core.shopping import format_qty
from meal_planner.db.repository import SettingsRepository

TODAY = date(2026, 11, 9)
DEFAULT_2X = settings_of(next(s for s in SCENARIOS if s["name"] == "default_2x"))


@pytest.fixture
def repo(tmp_path):
    return SettingsRepository(tmp_path / "db.sqlite")


def make_client(repo) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_today] = lambda: TODAY
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
    assert body["version"] == 0
    assert body["settings"] == {
        "meal_anchor_date": "2026-11-10",
        "cook_count": 2,
        "cook_weekday1": 3,
        "cook_weekday2": 6,
        "shopping_timing": "same",
    }
    assert [d["date"] for d in body["schedule"]][:2] == ["2026-11-09", "2026-11-10"]
    assert len(body["schedule"]) == 14
    assert len(body["recipes"]) == 12
    assert repo.get_settings() is None  # дефолты в БД не пишутся


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
    recipes = body["recipes"]
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
