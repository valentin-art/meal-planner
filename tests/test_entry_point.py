import uvicorn

import meal_planner


def test_runs_uvicorn_on_port_from_env(monkeypatch):
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append((app, kw)))
    monkeypatch.setenv("MEALPLAN_PORT", "8123")
    meal_planner.main()
    assert calls == [("meal_planner.api.main:app", {"host": "0.0.0.0", "port": 8123})]


def test_default_port(monkeypatch):
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append(kw["port"]))
    monkeypatch.delenv("MEALPLAN_PORT", raising=False)
    meal_planner.main()
    assert calls == [8000]
