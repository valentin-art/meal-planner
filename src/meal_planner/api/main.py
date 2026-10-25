"""HTTP-слой: только вызывает core/ и db/, бизнес-логики здесь нет.

Запуск: uv run uvicorn meal_planner.api.main:app
"""

import os
from collections.abc import Callable
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Query
from fastapi.staticfiles import StaticFiles

from meal_planner.api.schemas import (
    AppStateOut,
    RecipeOut,
    SetAnchorDateIn,
    SetCookScheduleIn,
    SetShoppingTimingIn,
    to_app_state_out,
    to_recipes_out,
)
from meal_planner.core import actions
from meal_planner.core.models import Settings, default_settings
from meal_planner.core.planner import recompute
from meal_planner.core.recipes_data import ALL_RECIPES
from meal_planner.db.repository import SettingsRepository, db_path_from_env

DEFAULT_STATIC_DIR = Path("frontend/dist")


@lru_cache
def get_repository() -> SettingsRepository:
    return SettingsRepository(db_path_from_env())


def get_today() -> date:
    return date.today()


Repo = Annotated[SettingsRepository, Depends(get_repository)]
Today = Annotated[date, Depends(get_today)]
DateFrom = Annotated[date | None, Query(alias="from")]
DateTo = Annotated[date | None, Query(alias="to")]


def current_settings(repo: SettingsRepository, today: date) -> tuple[Settings, int]:
    stored = repo.get_settings()
    if stored is None:
        # Как в JS: пока ничего не сохранено — дефолты, в БД не пишем.
        return default_settings(today), 0
    return stored.settings, stored.version


def state_response(
    settings: Settings,
    version: int,
    today: date,
    date_from: date | None,
    date_to: date | None,
) -> AppStateOut:
    return to_app_state_out(recompute(settings, today, date_from, date_to, version))


def apply_action(
    repo: SettingsRepository,
    today: date,
    date_from: date | None,
    date_to: date | None,
    change: Callable[[Settings], Settings],
) -> AppStateOut:
    # Чтение и запись не в одной транзакции — для одного пользователя гонки
    # не важны. Первое действие сохраняет дефолты + изменение, как первый
    # persist({...DEFAULT_SETTINGS, поле}) в JS.
    settings, _ = current_settings(repo, today)
    settings = change(settings)
    version = repo.save_settings(settings)
    return state_response(settings, version, today, date_from, date_to)


def create_app() -> FastAPI:
    app = FastAPI(title="Meal planner")

    @app.get("/api/state", response_model=AppStateOut)
    def get_state(
        repo: Repo, today: Today, date_from: DateFrom = None, date_to: DateTo = None
    ) -> AppStateOut:
        settings, version = current_settings(repo, today)
        return state_response(settings, version, today, date_from, date_to)

    @app.get("/api/recipes", response_model=dict[str, RecipeOut])
    def get_recipes() -> dict[str, RecipeOut]:
        # Справочник статичен: ни настройки, ни БД не нужны.
        return to_recipes_out(ALL_RECIPES)

    @app.post("/api/actions/set-cook-schedule", response_model=AppStateOut)
    def set_cook_schedule(
        body: SetCookScheduleIn,
        repo: Repo,
        today: Today,
        date_from: DateFrom = None,
        date_to: DateTo = None,
    ) -> AppStateOut:
        return apply_action(
            repo,
            today,
            date_from,
            date_to,
            lambda s: actions.set_cook_schedule(
                s, body.cook_count, body.cook_weekday1, body.cook_weekday2
            ),
        )

    @app.post("/api/actions/set-shopping-timing", response_model=AppStateOut)
    def set_shopping_timing(
        body: SetShoppingTimingIn,
        repo: Repo,
        today: Today,
        date_from: DateFrom = None,
        date_to: DateTo = None,
    ) -> AppStateOut:
        return apply_action(
            repo,
            today,
            date_from,
            date_to,
            lambda s: actions.set_shopping_timing(s, body.shopping_timing),
        )

    @app.post("/api/actions/set-anchor-date", response_model=AppStateOut)
    def set_anchor_date(
        body: SetAnchorDateIn,
        repo: Repo,
        today: Today,
        date_from: DateFrom = None,
        date_to: DateTo = None,
    ) -> AppStateOut:
        return apply_action(
            repo,
            today,
            date_from,
            date_to,
            lambda s: actions.set_anchor_date(s, body.meal_anchor_date),
        )

    # Монтируется последним, чтобы не перекрывать /api.
    static_dir = Path(os.environ.get("MEALPLAN_STATIC_DIR", DEFAULT_STATIC_DIR))
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app


app = create_app()
