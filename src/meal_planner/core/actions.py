"""Атомарные действия над настройками: что каждое действие меняет.

Чистые функции Settings → Settings; HTTP-слой (и любой другой клиент) только
вызывает их и сохраняет результат. Как в JS persist({...settings, поле}):
меняются только поля действия, остальные (включая якорь) остаются.
"""

from dataclasses import replace

from meal_planner.core.models import Settings, ShoppingTiming


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
