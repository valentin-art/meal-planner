"""Ответы API для снапшотов UI (frontend/src/ui-snapshots.test.tsx).

То, что вернул бы сервер на запросы клиента при данных настройках и «сегодня»:
начальный getState(), «Показать ещё» (28 дней), getRecipes() и
getIngredients().

Запуск: cd frontend && npm run gen:fixtures. Сервер не нужен.
"""

import json
from dataclasses import replace
from datetime import date, timedelta

from meal_planner.api.schemas import (
    to_app_state_out,
    to_ingredients_out,
    to_recipes_out,
)
from meal_planner.core.catalog import build_catalog, spec_of
from meal_planner.core.catalog_actions import (
    NewIngredient,
    ProposedLine,
    RecipeProposal,
    propose_recipe,
)
from meal_planner.core.models import (
    Catalog,
    CycleShuffle,
    MealKind,
    PortionScale,
    RecipeStorage,
    Settings,
    SlotVersion,
    default_settings,
)
from meal_planner.core.planner import recompute
from meal_planner.core.seed import SEED_CATALOG

PAGE_SIZE = 14  # как PAGE_SIZE/LOAD_STEP во фронтенде

# Черновик ужина от Assist (8.6a) — только в справочнике рецептов: в план
# черновик не попадает, сценарии его не видят. Паприка — новый ингредиент
# (8.6b), баклажан «1 шт» и «0.15 кг» нута переводит core; на существующие
# блюда не похож (8.6c).
PROPOSED = propose_recipe(
    SEED_CATALOG,
    RecipeProposal(
        name="Нут с баклажаном и фетой",
        kind=MealKind.DINNER,
        ingredients=(
            ProposedLine("нут консервированный", 0.15, "кг"),
            ProposedLine("баклажан", 1, "шт"),
            ProposedLine("фета", 40, "г"),
            ProposedLine("масло растительное", 1, "ст.л."),
            ProposedLine("паприка копчёная", 0.5, "ч.л."),
        ),
        steps=("Запечь баклажан с паприкой.", "Смешать с нутом, сверху — фета."),
        storage=RecipeStorage(
            "до 4 дней в холодильнике", "5–7 минут под крышкой", "2–3 минуты"
        ),
        new_ingredients=(
            NewIngredient(
                "паприка копчёная", "ч.л.", "Прочее", 282, 14.1, grams_per_unit=2.3
            ),
        ),
    ),
)
RECIPES_CATALOG = build_catalog(
    [*SEED_CATALOG.ingredients.values(), *PROPOSED.ingredients],
    [*map(spec_of, SEED_CATALOG.recipes.values()), PROPOSED.recipe],
    SEED_CATALOG.slot_versions,
)

# Позиция 1 с двумя обедами с цикла 3 (8.4b): l4 стоит и в позиции 4 —
# новых рецептов не нужно, справочник общий для всех сценариев.
SLOT_POOL_CATALOG = replace(
    SEED_CATALOG,
    slot_versions=(
        SlotVersion(1, 0, ("l1",), ("d1",)),
        SlotVersion(1, 3, ("l1", "l4"), ("d1",)),
        *SEED_CATALOG.slot_versions[1:],
    ),
)

# (имя, сегодня, сохранённые настройки или None — первый запуск: первый GET
# сохраняет дефолты, version 1[, каталог — по умолчанию сид])
SCENARIOS: list[tuple] = [
    # день 0 сегодня: якорь по умолчанию — завтра
    ("defaults", date(2026, 11, 9), None),
    # сегодня — день готовки
    ("default_2x", date(2026, 11, 11), Settings(date(2026, 11, 5), 2, 3, 6, "same")),
    (
        "default_2x_before",
        date(2026, 11, 10),
        Settings(date(2026, 11, 5), 2, 3, 6, "before"),
    ),
    # окно 7 дней, второй день задан, но не используется
    (
        "single_1x_sunday",
        date(2026, 11, 4),
        Settings(date(2026, 11, 2), 1, 7, 6, "same"),
    ),
    # окна 1 и 6 дней, закупка за день до
    ("adjacent_2x", date(2027, 3, 29), Settings(date(2027, 3, 25), 2, 1, 2, "before")),
    # якорь посреди окна: даты до якоря, день 0 (Вс) — не день готовки
    (
        "anchor_mid_window",
        date(2026, 11, 6),
        Settings(date(2026, 11, 9), 2, 3, 6, "same"),
    ),
    # готовка Ср 11.11 пропущена (7.7): пауза Чт–Сб, закупка — к Сб
    (
        "skipped_2x",
        date(2026, 11, 9),
        Settings(date(2026, 11, 5), 2, 3, 6, "same", date(2026, 11, 11)),
    ),
    # цель 2000 ккал и 100 г белка (8.1): отклонение в итоге дня
    (
        "nutrition_target",
        date(2026, 11, 11),
        Settings(date(2026, 11, 5), 2, 3, 6, "same", None, 2000, 100),
    ),
    # порядок перемешан с цикла 1 (8.4a), новое зерно — с цикла 3: его
    # партия начинается чт 19.11
    (
        "cycle_shuffled",
        date(2026, 11, 9),
        Settings(
            date(2026, 11, 5),
            2,
            3,
            6,
            "same",
            cycle_shuffles=(CycleShuffle(1, 42), CycleShuffle(3, 7)),
        ),
    ),
    # два обеда в позиции 1 с цикла 3: партия с чт 19.11 (8.4b)
    (
        "slot_pool",
        date(2026, 11, 9),
        Settings(date(2026, 11, 5), 2, 3, 6, "same"),
        SLOT_POOL_CATALOG,
    ),
    # порции под 2700 ккал (8.5), с партии вс 15.11 — под 2400
    (
        "portion_scaling",
        date(2026, 11, 9),
        Settings(
            date(2026, 11, 5),
            2,
            3,
            6,
            "same",
            daily_kcal_target=2400,
            portion_scales=(
                PortionScale(date(2026, 11, 5), 2700),
                PortionScale(date(2026, 11, 15), 2400),
            ),
        ),
    ),
]


def state_json(
    catalog: Catalog,
    settings: Settings,
    today: date,
    version: int,
    date_from=None,
    date_to=None,
) -> dict:
    out = to_app_state_out(
        recompute(catalog, settings, today, date_from, date_to, version), catalog
    )
    return out.model_dump(mode="json")


def build() -> dict:
    scenarios = []
    for name, today, saved, *rest in SCENARIOS:
        catalog = rest[0] if rest else SEED_CATALOG
        settings = saved or default_settings(today)
        more_to = today + timedelta(days=2 * PAGE_SIZE - 1)
        responses = {
            "api/state": state_json(catalog, settings, today, 1),
            f"api/state?from={today}&to={more_to}": state_json(
                catalog, settings, today, 1, today, more_to
            ),
        }
        scenarios.append(
            {
                "name": name,
                "today": today.isoformat(),
                "responses": responses,
            }
        )
    # Ответ GET /api/recipes — один на все сценарии.
    recipes = {
        rid: r.model_dump(mode="json")
        for rid, r in to_recipes_out(RECIPES_CATALOG).items()
    }
    # Ответ GET /api/ingredients — для редактора рецепта (5.5).
    ingredients = to_ingredients_out(RECIPES_CATALOG).model_dump(mode="json")
    return {"recipes": recipes, "ingredients": ingredients, "scenarios": scenarios}


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=1))
