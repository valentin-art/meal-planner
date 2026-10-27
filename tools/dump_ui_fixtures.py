"""Ответы API для снапшотов UI (frontend/src/ui-snapshots.test.tsx).

То, что вернул бы сервер на запросы клиента при данных настройках и «сегодня»:
начальный getState(), «Показать ещё» (28 дней), getRecipes() и
getIngredients().

Запуск: cd frontend && npm run gen:fixtures. Сервер не нужен.
"""

import json
from datetime import date, timedelta

from meal_planner.api.schemas import (
    to_app_state_out,
    to_ingredients_out,
    to_recipes_out,
)
from meal_planner.core.models import Settings, default_settings
from meal_planner.core.planner import recompute
from meal_planner.core.seed import SEED_CATALOG

PAGE_SIZE = 14  # как PAGE_SIZE/LOAD_STEP во фронтенде

# (имя, сегодня, сохранённые настройки или None — первый запуск: первый GET
# сохраняет дефолты, version 1)
SCENARIOS = [
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
]


def state_json(
    settings: Settings, today: date, version: int, date_from=None, date_to=None
) -> dict:
    out = to_app_state_out(
        recompute(SEED_CATALOG, settings, today, date_from, date_to, version)
    )
    return out.model_dump(mode="json")


def build() -> dict:
    scenarios = []
    for name, today, saved in SCENARIOS:
        settings = saved or default_settings(today)
        more_to = today + timedelta(days=2 * PAGE_SIZE - 1)
        responses = {
            "api/state": state_json(settings, today, 1),
            f"api/state?from={today}&to={more_to}": state_json(
                settings, today, 1, today, more_to
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
        for rid, r in to_recipes_out(SEED_CATALOG).items()
    }
    # Ответ GET /api/ingredients — для редактора рецепта (5.5).
    ingredients = to_ingredients_out(SEED_CATALOG).model_dump(mode="json")
    return {"recipes": recipes, "ingredients": ingredients, "scenarios": scenarios}


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=1))
