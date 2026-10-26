"""HTTP-слой: только вызывает core/ и db/, бизнес-логики здесь нет.

Запуск: uv run uvicorn meal_planner.api.main:app
"""

import logging
import os
from collections.abc import Callable
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from meal_planner.api.schemas import (
    AppStateOut,
    HealthOut,
    RecipeOut,
    SetAnchorDateIn,
    SetCookScheduleIn,
    SetCyclePositionIn,
    SetShoppingTimingIn,
    SummaryOut,
    to_app_state_out,
    to_recipes_out,
    to_summary_out,
)
from meal_planner.core import actions
from meal_planner.core.models import Settings, default_settings
from meal_planner.core.planner import recompute
from meal_planner.core.recipes_data import ALL_RECIPES
from meal_planner.core.validation import settings_errors, usable_settings
from meal_planner.db.repository import (
    SettingsRepository,
    StoredSettings,
    db_path_from_env,
)

logger = logging.getLogger(__name__)

DEFAULT_STATIC_DIR = Path("frontend/dist")
MAX_RANGE_DAYS = 366  # ограничение API (?from=&to=), не бизнес-правило


@lru_cache
def get_repository() -> SettingsRepository:
    return SettingsRepository(db_path_from_env())


def get_today() -> date:
    return date.today()


Repo = Annotated[SettingsRepository, Depends(get_repository)]
Today = Annotated[date, Depends(get_today)]
DateFrom = Annotated[date | None, Query(alias="from")]
DateTo = Annotated[date | None, Query(alias="to")]


def checked(stored: StoredSettings, today: date) -> Settings:
    settings, errors = usable_settings(stored.settings, today)
    if errors:
        logger.warning(
            "Сохранённые настройки невалидны (%s): расписание — по дефолтам"
            " с сохранённым якорем, пока их не перезапишет действие",
            "; ".join(f"{e.field}: {e.message}" for e in errors),
        )
    return settings


def current_settings(repo: SettingsRepository, today: date) -> tuple[Settings, int]:
    stored = repo.get_settings()
    if stored is None:
        # Для действий: дефолты в памяти, пишутся вместе с изменением, а
        # невалидное действие не пишет ничего.
        return default_settings(today), 0
    return checked(stored, today), stored.version


def stored_or_initial(repo: SettingsRepository, today: date) -> tuple[Settings, int]:
    # Первый GET сохраняет дефолты: иначе якорь («завтра») пересчитывался бы
    # от каждого нового «сегодня» и цикл начинался заново (6.4).
    stored = repo.get_settings() or repo.init_settings(default_settings(today))
    return checked(stored, today), stored.version


def state_response(
    settings: Settings,
    version: int,
    today: date,
    date_from: date | None,
    date_to: date | None,
) -> AppStateOut:
    return to_app_state_out(recompute(settings, today, date_from, date_to, version))


def unprocessable(*errors: tuple[list[str], str]) -> HTTPException:
    # Тот же формат detail, что у ошибок валидации Pydantic/FastAPI.
    return HTTPException(
        422,
        detail=[{"type": "value_error", "loc": loc, "msg": msg} for loc, msg in errors],
    )


def check_range(today: date, date_from: date | None, date_to: date | None) -> None:
    # Без ?to= recompute берёт 14 дней — проверять нечего.
    if date_to is None:
        return
    start = date_from or today
    if start > date_to:
        raise unprocessable(
            (["query", "from"], "Начало диапазона (from) позже конца (to)")
        )
    if (date_to - start).days + 1 > MAX_RANGE_DAYS:
        raise unprocessable(
            (["query", "to"], f"Диапазон дат — не больше {MAX_RANGE_DAYS} дней")
        )


def apply_action(
    repo: SettingsRepository,
    today: date,
    date_from: date | None,
    date_to: date | None,
    change: Callable[[Settings], Settings],
) -> AppStateOut:
    # Всё проверяется до save_settings: невалидный запрос ничего не пишет.
    check_range(today, date_from, date_to)
    # Чтение и запись не в одной транзакции — для одного пользователя гонки
    # не важны. Первое действие сохраняет дефолты + изменение, как первый
    # persist({...DEFAULT_SETTINGS, поле}) в JS.
    settings, _ = current_settings(repo, today)
    try:
        settings = change(settings)
    except actions.ActionError as e:
        raise unprocessable((["body", e.field], e.message)) from e
    errors = settings_errors(settings)
    if errors:
        raise unprocessable(*((["body", e.field], e.message) for e in errors))
    version = repo.save_settings(settings)
    return state_response(settings, version, today, date_from, date_to)


def create_app() -> FastAPI:
    app = FastAPI(title="Meal planner")

    @app.get("/api/health", response_model=HealthOut)
    def health(repo: Repo) -> HealthOut:
        # Для HEALTHCHECK в Dockerfile: сервер отвечает и БД читается.
        # Только чтение — дефолты пишет первый GET /api/state, не проверка.
        repo.get_settings()
        return HealthOut(status="ok")

    @app.get("/api/state", response_model=AppStateOut)
    def get_state(
        repo: Repo, today: Today, date_from: DateFrom = None, date_to: DateTo = None
    ) -> AppStateOut:
        check_range(today, date_from, date_to)
        settings, version = stored_or_initial(repo, today)
        return state_response(settings, version, today, date_from, date_to)

    @app.get("/api/summary", response_model=SummaryOut)
    def get_summary(repo: Repo, today: Today) -> SummaryOut:
        # Для HA: сегодня и завтра + ближайшая закупка. Первый запрос, как и
        # GET /api/state, сохраняет дефолты — иначе якорь плыл бы (6.4).
        settings, version = stored_or_initial(repo, today)
        return to_summary_out(
            recompute(settings, today, today, today + timedelta(days=1), version)
        )

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

    @app.post("/api/actions/set-cycle-position", response_model=AppStateOut)
    def set_cycle_position(
        body: SetCyclePositionIn,
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
            lambda s: actions.set_cycle_position(s, today, body.dish),
        )

    # Монтируется последним, чтобы не перекрывать /api.
    static_dir = Path(os.environ.get("MEALPLAN_STATIC_DIR", DEFAULT_STATIC_DIR))
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app


app = create_app()
