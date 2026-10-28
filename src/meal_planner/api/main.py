"""HTTP-слой: только вызывает core/ и db/, бизнес-логики здесь нет.

Запуск: uv run uvicorn meal_planner.api.main:app
"""

import logging
import os
import secrets
from collections.abc import Callable, Sequence
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from meal_planner.api.schemas import (
    AppStateOut,
    CookedActionOut,
    CookedIn,
    DishPortionsOut,
    HealthOut,
    IngredientActionOut,
    IngredientsOut,
    NutritionOut,
    RecipeActionOut,
    RecipeIdIn,
    RecipeOut,
    RecipePreviewIn,
    SaveIngredientIn,
    SaveRecipeIn,
    SetAnchorDateIn,
    SetCookScheduleIn,
    SetCyclePositionIn,
    SetCycleSlotIn,
    SetDishPortionsIn,
    SetNutritionTargetIn,
    SetPortionScalingIn,
    SetShoppingTimingIn,
    ShoppingRemainingIn,
    ShoppingRemainingOut,
    SummaryOut,
    to_app_state_out,
    to_catalog_ingredient_out,
    to_cooked_action_out,
    to_ingredients_out,
    to_recipe_out,
    to_recipes_out,
    to_shopping_remaining_out,
    to_summary_out,
)
from meal_planner.core import actions, catalog_actions, cooking
from meal_planner.core.catalog_actions import CatalogError
from meal_planner.core.models import (
    Catalog,
    RecipeSpec,
    Settings,
    default_settings,
)
from meal_planner.core.planner import recompute, shopping_with_stock
from meal_planner.core.scheduling import first_free_cycle, settle_skip
from meal_planner.core.validation import (
    SEED_LIMIT,
    settings_errors,
    usable_settings,
)
from meal_planner.db.repository import (
    Repository,
    StoredSettings,
    db_path_from_env,
)

logger = logging.getLogger(__name__)

DEFAULT_STATIC_DIR = Path("frontend/dist")
MAX_RANGE_DAYS = 366  # ограничение API (?from=&to=), не бизнес-правило


class FrontendFiles(StaticFiles):
    """Сборка фронтенда: index.html всегда сверяется с сервером.

    Без Cache-Control браузер кэширует по Last-Modified «на глаз» и часами
    отдаёт старый index.html со ссылкой на старый бандл — после обновления
    образа UI оставался прежним. Файлы в assets/ с хешем в имени не меняются.
    """

    def file_response(self, full_path, stat_result, scope, status_code=200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        hashed = Path(full_path).parent.name == "assets"
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable" if hashed else "no-cache"
        )
        return response


@lru_cache
def get_repository() -> Repository:
    return Repository(db_path_from_env())


def get_today() -> date:
    return date.today()


Repo = Annotated[Repository, Depends(get_repository)]


def get_catalog(repo: Repo) -> Catalog:
    # Каталог — из БД на каждый запрос (5.3): маленький, а кэш пришлось бы
    # сбрасывать при правке рецептов.
    return repo.get_catalog()


CatalogDep = Annotated[Catalog, Depends(get_catalog)]
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


def current_settings(repo: Repository, today: date) -> tuple[Settings, int]:
    stored = repo.get_settings()
    if stored is None:
        # Для действий: дефолты в памяти, пишутся вместе с изменением, а
        # невалидное действие не пишет ничего.
        return default_settings(today), 0
    return checked(stored, today), stored.version


def stored_or_initial(repo: Repository, today: date) -> tuple[Settings, int]:
    # Первый GET сохраняет дефолты: иначе якорь («завтра») пересчитывался бы
    # от каждого нового «сегодня» и цикл начинался заново (6.4).
    stored = repo.get_settings() or repo.init_settings(default_settings(today))
    return checked(stored, today), stored.version


def state_response(
    catalog: Catalog,
    settings: Settings,
    version: int,
    today: date,
    date_from: date | None,
    date_to: date | None,
) -> AppStateOut:
    return to_app_state_out(
        recompute(catalog, settings, today, date_from, date_to, version), catalog
    )


def unprocessable(*errors: tuple[Sequence[str | int], str]) -> HTTPException:
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
    repo: Repository,
    catalog: Catalog,
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
    return state_response(catalog, settings, version, today, date_from, date_to)


def apply_catalog_action[T](
    repo: Repository,
    catalog: Catalog,
    today: date,
    date_from: date | None,
    date_to: date | None,
    change: Callable[[Catalog, int], T],
    write: Callable[[T], int],
) -> tuple[AppStateOut, Catalog, T]:
    """Правка каталога (5.4): core проверяет, репозиторий пишет с версией.

    change получает и первый некупленный цикл: с него меняется состав
    позиций (8.4b). Возвращает новое состояние, перечитанный каталог (ккал
    пересчитаны) и то, что записано.
    """
    check_range(today, date_from, date_to)
    current, _ = current_settings(repo, today)
    first = first_free_cycle(settle_skip(current, today), today)
    try:
        result = change(catalog, first)
    except CatalogError as e:
        raise unprocessable(
            *((["body", *err.loc], err.message) for err in e.errors)
        ) from e
    # Версия — в строке настроек: до первой записи её создаёт первый GET,
    # здесь — то же самое, но только для валидного запроса.
    settings, _ = stored_or_initial(repo, today)
    version = write(result)
    updated = repo.get_catalog()
    state = state_response(updated, settings, version, today, date_from, date_to)
    return state, updated, result


def recipe_action(
    repo: Repository,
    catalog: Catalog,
    today: date,
    date_from: date | None,
    date_to: date | None,
    change: Callable[[Catalog], RecipeSpec],
) -> RecipeActionOut:
    """Правка рецепта и, если нужно, состава позиций (8.4b) — одной записью."""
    state, updated, result = apply_catalog_action(
        repo,
        catalog,
        today,
        date_from,
        date_to,
        lambda c, first: catalog_actions.plan_for(c, change(c), first),
        lambda r: repo.save_recipe(r.recipe, r.slot_versions),
    )
    return RecipeActionOut(
        state=state, recipe=to_recipe_out(updated, updated.recipes[result.recipe.id])
    )


def cooked_action(
    repo: Repository, catalog: Catalog, today: date, body: CookedIn, mark: bool
) -> CookedActionOut:
    """Отметить или снять «приготовлено» (8.3b) и посчитать новые остатки.

    Значения счётчиков выставляет HA; при ошибке ничего не пишется.
    """
    # Версия — в строке настроек: до первой записи её создаёт первый GET.
    settings, _ = stored_or_initial(repo, today)
    lines = body.to_lines()
    try:
        change = cooking.cooked_change(
            catalog,
            settings,
            today,
            repo.get_cooked(),
            body.recipe_id,
            mark,
            repo.get_dish_portions(),
        )
        if mark:
            # Не хватает продуктов — блюдо не отмечается и ничего не списано.
            cooking.check_enough(catalog, change.recipe, change.portions, lines)
    except actions.ActionError as e:
        raise unprocessable((["body", e.field], e.message)) from e
    update = cooking.counter_update(
        catalog, change.recipe, change.portions, lines, -1 if mark else 1
    )
    write = repo.mark_cooked if mark else repo.unmark_cooked
    version = write(change.day, body.recipe_id)
    return to_cooked_action_out(version, change.day, body.recipe_id, update)


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
        repo: Repo,
        catalog: CatalogDep,
        today: Today,
        date_from: DateFrom = None,
        date_to: DateTo = None,
    ) -> AppStateOut:
        check_range(today, date_from, date_to)
        settings, version = stored_or_initial(repo, today)
        return state_response(catalog, settings, version, today, date_from, date_to)

    @app.get("/api/summary", response_model=SummaryOut)
    def get_summary(repo: Repo, catalog: CatalogDep, today: Today) -> SummaryOut:
        # Для HA: сегодня и завтра + ближайшая закупка. Первый запрос, как и
        # GET /api/state, сохраняет дефолты — иначе якорь плыл бы (6.4).
        settings, version = stored_or_initial(repo, today)
        return to_summary_out(
            recompute(
                catalog, settings, today, today, today + timedelta(days=1), version
            ),
            cooking.cooking(
                catalog, settings, today, repo.get_cooked(), repo.get_dish_portions()
            ),
        )

    @app.post("/api/shopping/remaining", response_model=ShoppingRemainingOut)
    def shopping_remaining(
        body: ShoppingRemainingIn, repo: Repo, catalog: CatalogDep, today: Today
    ) -> ShoppingRemainingOut:
        # Для HA (8.2): ближайшая закупка за вычетом его остатков. Только
        # чтение — остатки не сохраняются, версия не меняется; POST из-за
        # тела, это не действие. Вкладка «Закупки» остаётся полным списком.
        settings, _ = stored_or_initial(repo, today)
        return to_shopping_remaining_out(
            shopping_with_stock(catalog, settings, today, body.to_lines())
        )

    @app.get("/api/recipes", response_model=dict[str, RecipeOut])
    def get_recipes(catalog: CatalogDep) -> dict[str, RecipeOut]:
        # Только каталог: настройки не нужны и не пишутся.
        return to_recipes_out(catalog)

    @app.post("/api/recipes/preview", response_model=NutritionOut)
    def preview_recipe(body: RecipePreviewIn, catalog: CatalogDep) -> NutritionOut:
        # Ккал черновика для редактора (5.5): только чтение — ничего не пишет,
        # версия не меняется. POST из-за тела, это не действие.
        kcal, protein = catalog_actions.preview_nutrition(catalog, body.to_lines())
        return NutritionOut(kcal=kcal, protein=protein)

    @app.get("/api/ingredients", response_model=IngredientsOut)
    def get_ingredients(catalog: CatalogDep) -> IngredientsOut:
        return to_ingredients_out(catalog)

    @app.post("/api/actions/set-cook-schedule", response_model=AppStateOut)
    def set_cook_schedule(
        body: SetCookScheduleIn,
        repo: Repo,
        catalog: CatalogDep,
        today: Today,
        date_from: DateFrom = None,
        date_to: DateTo = None,
    ) -> AppStateOut:
        return apply_action(
            repo,
            catalog,
            today,
            date_from,
            date_to,
            lambda s: actions.set_cook_schedule(
                s, today, body.cook_count, body.cook_weekday1, body.cook_weekday2
            ),
        )

    @app.post("/api/actions/set-shopping-timing", response_model=AppStateOut)
    def set_shopping_timing(
        body: SetShoppingTimingIn,
        repo: Repo,
        catalog: CatalogDep,
        today: Today,
        date_from: DateFrom = None,
        date_to: DateTo = None,
    ) -> AppStateOut:
        return apply_action(
            repo,
            catalog,
            today,
            date_from,
            date_to,
            lambda s: actions.set_shopping_timing(s, body.shopping_timing),
        )

    @app.post("/api/actions/set-nutrition-target", response_model=AppStateOut)
    def set_nutrition_target(
        body: SetNutritionTargetIn,
        repo: Repo,
        catalog: CatalogDep,
        today: Today,
        date_from: DateFrom = None,
        date_to: DateTo = None,
    ) -> AppStateOut:
        return apply_action(
            repo,
            catalog,
            today,
            date_from,
            date_to,
            lambda s: actions.set_nutrition_target(
                s, today, body.daily_kcal_target, body.daily_protein_target
            ),
        )

    @app.post("/api/actions/set-portion-scaling", response_model=AppStateOut)
    def set_portion_scaling(
        body: SetPortionScalingIn,
        repo: Repo,
        catalog: CatalogDep,
        today: Today,
        date_from: DateFrom = None,
        date_to: DateTo = None,
    ) -> AppStateOut:
        return apply_action(
            repo,
            catalog,
            today,
            date_from,
            date_to,
            lambda s: actions.set_portion_scaling(s, today, body.enabled),
        )

    @app.post("/api/actions/set-anchor-date", response_model=AppStateOut)
    def set_anchor_date(
        body: SetAnchorDateIn,
        repo: Repo,
        catalog: CatalogDep,
        today: Today,
        date_from: DateFrom = None,
        date_to: DateTo = None,
    ) -> AppStateOut:
        return apply_action(
            repo,
            catalog,
            today,
            date_from,
            date_to,
            lambda s: actions.set_anchor_date(s, body.meal_anchor_date),
        )

    @app.post("/api/actions/set-cycle-position", response_model=AppStateOut)
    def set_cycle_position(
        body: SetCyclePositionIn,
        repo: Repo,
        catalog: CatalogDep,
        today: Today,
        date_from: DateFrom = None,
        date_to: DateTo = None,
    ) -> AppStateOut:
        return apply_action(
            repo,
            catalog,
            today,
            date_from,
            date_to,
            lambda s: actions.set_cycle_position(catalog, s, today, body.dish),
        )

    @app.post("/api/actions/skip-next-cook", response_model=AppStateOut)
    def skip_next_cook(
        repo: Repo,
        catalog: CatalogDep,
        today: Today,
        date_from: DateFrom = None,
        date_to: DateTo = None,
    ) -> AppStateOut:
        return apply_action(
            repo,
            catalog,
            today,
            date_from,
            date_to,
            lambda s: actions.skip_next_cook(s, today),
        )

    @app.post("/api/actions/unskip-cook", response_model=AppStateOut)
    def unskip_cook(
        repo: Repo,
        catalog: CatalogDep,
        today: Today,
        date_from: DateFrom = None,
        date_to: DateTo = None,
    ) -> AppStateOut:
        return apply_action(
            repo,
            catalog,
            today,
            date_from,
            date_to,
            lambda s: actions.unskip_cook(s, today),
        )

    @app.post("/api/actions/reshuffle-cycle", response_model=AppStateOut)
    def reshuffle_cycle(
        repo: Repo,
        catalog: CatalogDep,
        today: Today,
        date_from: DateFrom = None,
        date_to: DateTo = None,
    ) -> AppStateOut:
        seed = secrets.randbelow(SEED_LIMIT)
        return apply_action(
            repo,
            catalog,
            today,
            date_from,
            date_to,
            lambda s: actions.reshuffle_cycle(s, today, seed),
        )

    @app.post("/api/actions/reset-cycle-order", response_model=AppStateOut)
    def reset_cycle_order(
        repo: Repo,
        catalog: CatalogDep,
        today: Today,
        date_from: DateFrom = None,
        date_to: DateTo = None,
    ) -> AppStateOut:
        return apply_action(
            repo,
            catalog,
            today,
            date_from,
            date_to,
            lambda s: actions.reset_cycle_order(s, today),
        )

    @app.post("/api/actions/mark-cooked", response_model=CookedActionOut)
    def mark_cooked(
        body: CookedIn, repo: Repo, catalog: CatalogDep, today: Today
    ) -> CookedActionOut:
        return cooked_action(repo, catalog, today, body, mark=True)

    @app.post("/api/actions/unmark-cooked", response_model=CookedActionOut)
    def unmark_cooked(
        body: CookedIn, repo: Repo, catalog: CatalogDep, today: Today
    ) -> CookedActionOut:
        return cooked_action(repo, catalog, today, body, mark=False)

    @app.post("/api/actions/set-dish-portions", response_model=DishPortionsOut)
    def set_dish_portions(
        body: SetDishPortionsIn, repo: Repo, catalog: CatalogDep, today: Today
    ) -> DishPortionsOut:
        # Только списание (8.3b): план и закупка от порций не зависят.
        settings, _ = stored_or_initial(repo, today)
        try:
            day = cooking.set_portions(
                catalog,
                settings,
                today,
                repo.get_cooked(),
                body.recipe_id,
                body.portions,
            )
        except actions.ActionError as e:
            raise unprocessable((["body", e.field], e.message)) from e
        version = repo.set_dish_portions(day, body.recipe_id, body.portions)
        return DishPortionsOut(
            version=version, day=day, recipe_id=body.recipe_id, portions=body.portions
        )

    @app.post("/api/actions/save-recipe", response_model=RecipeActionOut)
    def save_recipe(
        body: SaveRecipeIn,
        repo: Repo,
        catalog: CatalogDep,
        today: Today,
        date_from: DateFrom = None,
        date_to: DateTo = None,
    ) -> RecipeActionOut:
        return recipe_action(
            repo,
            catalog,
            today,
            date_from,
            date_to,
            lambda c: catalog_actions.save_recipe(c, body.to_input()),
        )

    @app.post("/api/actions/archive-recipe", response_model=RecipeActionOut)
    def archive_recipe(
        body: RecipeIdIn,
        repo: Repo,
        catalog: CatalogDep,
        today: Today,
        date_from: DateFrom = None,
        date_to: DateTo = None,
    ) -> RecipeActionOut:
        return recipe_action(
            repo,
            catalog,
            today,
            date_from,
            date_to,
            lambda c: catalog_actions.archive_recipe(c, body.id),
        )

    @app.post("/api/actions/restore-recipe", response_model=RecipeActionOut)
    def restore_recipe(
        body: RecipeIdIn,
        repo: Repo,
        catalog: CatalogDep,
        today: Today,
        date_from: DateFrom = None,
        date_to: DateTo = None,
    ) -> RecipeActionOut:
        return recipe_action(
            repo,
            catalog,
            today,
            date_from,
            date_to,
            lambda c: catalog_actions.restore_recipe(c, body.id),
        )

    @app.post("/api/actions/set-cycle-slot", response_model=AppStateOut)
    def set_cycle_slot(
        body: SetCycleSlotIn,
        repo: Repo,
        catalog: CatalogDep,
        today: Today,
        date_from: DateFrom = None,
        date_to: DateTo = None,
    ) -> AppStateOut:
        state, _, _ = apply_catalog_action(
            repo,
            catalog,
            today,
            date_from,
            date_to,
            lambda c, first: catalog_actions.set_cycle_slot(
                c, body.slot, tuple(body.lunch_ids), tuple(body.dinner_ids), first
            ),
            repo.save_slot_versions,
        )
        return state

    @app.post("/api/actions/save-ingredient", response_model=IngredientActionOut)
    def save_ingredient(
        body: SaveIngredientIn,
        repo: Repo,
        catalog: CatalogDep,
        today: Today,
        date_from: DateFrom = None,
        date_to: DateTo = None,
    ) -> IngredientActionOut:
        state, updated, saved = apply_catalog_action(
            repo,
            catalog,
            today,
            date_from,
            date_to,
            lambda c, _: catalog_actions.save_ingredient(c, body.to_input()),
            repo.save_ingredient,
        )
        ingredient = next(i for i in updated.ingredients.values() if i.id == saved.id)
        return IngredientActionOut(
            state=state, ingredient=to_catalog_ingredient_out(updated, ingredient)
        )

    # Монтируется последним, чтобы не перекрывать /api.
    static_dir = Path(os.environ.get("MEALPLAN_STATIC_DIR", DEFAULT_STATIC_DIR))
    if static_dir.is_dir():
        app.mount("/", FrontendFiles(directory=static_dir, html=True), name="static")
    return app


app = create_app()
