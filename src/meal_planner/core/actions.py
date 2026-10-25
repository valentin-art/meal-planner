"""Атомарные действия над настройками: что каждое действие меняет.

Чистые функции Settings → Settings; HTTP-слой (и любой другой клиент) только
вызывает их и сохраняет результат. Как в JS persist({...settings, поле}):
меняются только поля действия, остальные (включая якорь) остаются.
"""

from dataclasses import replace
from datetime import date

from meal_planner.core.models import Settings, ShoppingTiming
from meal_planner.core.scheduling import cook_weekdays, today_position


class ActionError(ValueError):
    """Действие невыполнимо при текущих настройках (API отвечает 422)."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field
        self.message = message


def set_anchor_date(settings: Settings, meal_anchor_date: date) -> Settings:
    # Любая дата, прошлая или будущая — как в JS. Пустую дату (в JS очищенный
    # <input type="date"> сохранял "" и ломал расписание) отсекает API.
    return replace(settings, meal_anchor_date=meal_anchor_date)


def set_cook_schedule(
    settings: Settings,
    cook_count: int,
    cook_weekday1: int,
    cook_weekday2: int | None = None,
) -> Settings:
    # cook_weekday2=None — оставить сохранённый: в JS второй день переживает
    # переключение 1 ↔ 2 готовки и восстанавливается при возврате к двум.
    return replace(
        settings,
        cook_count=cook_count,
        cook_weekday1=cook_weekday1,
        cook_weekday2=settings.cook_weekday2
        if cook_weekday2 is None
        else cook_weekday2,
    )


def set_shopping_timing(
    settings: Settings, shopping_timing: ShoppingTiming
) -> Settings:
    return replace(settings, shopping_timing=shopping_timing)


def set_cycle_position(settings: Settings, today: date, dish: int) -> Settings:
    """«Сегодня — блюдо №dish» (0 — сегодня день 0): сервер сам выбирает якорь."""
    options = today_position(
        today, settings.meal_anchor_date, cook_weekdays(settings)
    ).options
    anchor = next((o.anchor for o in options if o.dish == dish), None)
    if anchor is None:
        possible = ", ".join(
            "день 0" if o.dish == 0 else f"блюдо №{o.dish}" for o in options
        )
        raise ActionError("dish", f"Сегодня может быть только: {possible}")
    return replace(settings, meal_anchor_date=anchor)
