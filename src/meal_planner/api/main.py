"""HTTP-слой: только вызывает core/ и db/, бизнес-логики здесь нет.

Запуск: uv run uvicorn meal_planner.api.main:app
"""

import os
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Query
from fastapi.staticfiles import StaticFiles

from meal_planner.api.schemas import AppStateOut, to_app_state_out
from meal_planner.core.models import default_settings
from meal_planner.core.planner import recompute
from meal_planner.db.repository import SettingsRepository, db_path_from_env

DEFAULT_STATIC_DIR = Path("frontend/dist")


@lru_cache
def get_repository() -> SettingsRepository:
    return SettingsRepository(db_path_from_env())


def get_today() -> date:
    return date.today()


Repo = Annotated[SettingsRepository, Depends(get_repository)]
Today = Annotated[date, Depends(get_today)]


def create_app() -> FastAPI:
    app = FastAPI(title="Meal planner")

    @app.get("/api/state", response_model=AppStateOut)
    def get_state(
        repo: Repo,
        today: Today,
        date_from: Annotated[date | None, Query(alias="from")] = None,
        date_to: Annotated[date | None, Query(alias="to")] = None,
    ) -> AppStateOut:
        stored = repo.get_settings()
        if stored is None:
            # Как в JS: пока ничего не сохранено — дефолты, в БД не пишем.
            settings, version = default_settings(today), 0
        else:
            settings, version = stored.settings, stored.version
        return to_app_state_out(recompute(settings, today, date_from, date_to, version))

    # Монтируется последним, чтобы не перекрывать /api.
    static_dir = Path(os.environ.get("MEALPLAN_STATIC_DIR", DEFAULT_STATIC_DIR))
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app


app = create_app()
