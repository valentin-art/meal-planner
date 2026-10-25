"""Инварианты Settings — источник истины для любого клиента, не только HTTP.

Проверяются итоговые настройки (после применения действия): так ловится и
второй день готовки, которого нет ни в запросе, ни в сохранённых настройках.
"""

from dataclasses import dataclass, replace
from datetime import date

from meal_planner.core.models import Settings, default_settings

WEEKDAY_MESSAGE = "День готовки — число от 1 (пн) до 7 (вс)"


@dataclass(frozen=True, slots=True)
class SettingsError:
    field: str
    message: str


def settings_errors(settings: Settings) -> list[SettingsError]:
    errors = []
    if settings.cook_count not in (1, 2):
        errors.append(
            SettingsError("cook_count", "Готовок в неделю может быть 1 или 2")
        )
    if not 1 <= settings.cook_weekday1 <= 7:
        errors.append(SettingsError("cook_weekday1", WEEKDAY_MESSAGE))
    if settings.cook_count == 2:
        if settings.cook_weekday2 is None:
            errors.append(
                SettingsError(
                    "cook_weekday2",
                    "При двух готовках в неделю нужен второй день готовки",
                )
            )
        elif not 1 <= settings.cook_weekday2 <= 7:
            errors.append(SettingsError("cook_weekday2", WEEKDAY_MESSAGE))
        elif settings.cook_weekday2 == settings.cook_weekday1:
            # В JS допустимо (Чт/Чт = одна готовка с окном 7 дней) — запрещено с 6.8.
            errors.append(
                SettingsError("cook_weekday2", "Два дня готовки должны различаться")
            )
    if settings.shopping_timing not in ("same", "before"):
        errors.append(
            SettingsError("shopping_timing", "Закупка — в день готовки или за день до")
        )
    return errors


def usable_settings(
    settings: Settings, today: date
) -> tuple[Settings, list[SettingsError]]:
    """Сохранённые настройки, а если они невалидны (например, из старой версии
    или правлены руками) — дефолты с сохранённым якорем: цикл не сбрасывается."""
    errors = settings_errors(settings)
    if not errors:
        return settings, []
    fallback = replace(
        default_settings(today), meal_anchor_date=settings.meal_anchor_date
    )
    return fallback, errors
