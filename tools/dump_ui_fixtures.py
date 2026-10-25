"""Ответы /api/state для снапшотов UI (frontend/src/ui-snapshots.test.tsx).

То, что вернул бы сервер на запросы клиента при данных настройках и «сегодня»:
начальный getState() и «Показать ещё» (28 дней).

Запуск: cd frontend && npm run gen:fixtures. Сервер не нужен.
"""

import json
from datetime import date, timedelta

from meal_planner.api.schemas import to_app_state_out
from meal_planner.core.models import Settings, default_settings
from meal_planner.core.planner import recompute

PAGE_SIZE = 14  # как PAGE_SIZE/LOAD_STEP во фронтенде

# (имя, сегодня, сохранённые настройки или None — «ещё ничего не сохранено»)
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
]


def state_json(
    settings: Settings, today: date, version: int, date_from=None, date_to=None
) -> dict:
    out = to_app_state_out(recompute(settings, today, date_from, date_to, version))
    return out.model_dump(mode="json")


def build() -> dict:
    recipes = None
    scenarios = []
    for name, today, saved in SCENARIOS:
        settings = saved or default_settings(today)
        version = 0 if saved is None else 1
        more_to = today + timedelta(days=2 * PAGE_SIZE - 1)
        responses = {
            "api/state": state_json(settings, today, version),
            f"api/state?from={today}&to={more_to}": state_json(
                settings, today, version, today, more_to
            ),
        }
        for state in responses.values():
            recipes = state.pop("recipes")  # один справочник на все ответы
        scenarios.append(
            {
                "name": name,
                "today": today.isoformat(),
                "responses": responses,
            }
        )
    return {"recipes": recipes, "scenarios": scenarios}


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=1))
