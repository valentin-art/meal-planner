"""Инварианты Settings — источник истины для любого клиента, не только HTTP.

Проверяются итоговые настройки (после применения действия): так ловится и
второй день готовки, которого нет ни в запросе, ни в сохранённых настройках.
"""

from dataclasses import dataclass, replace
from datetime import date

from meal_planner.core.models import Settings, default_settings
from meal_planner.core.scheduling import cook_weekdays

WEEKDAY_MESSAGE = "День готовки — число от 1 (пн) до 7 (вс)"
KCAL_TARGET_MIN, KCAL_TARGET_MAX = 500, 6000
PROTEIN_TARGET_MIN, PROTEIN_TARGET_MAX = 10, 400


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
    # Пределы цели (8.1) — защита от опечатки (20000), не правило плана.
    kcal = settings.daily_kcal_target
    if kcal is not None and not KCAL_TARGET_MIN <= kcal <= KCAL_TARGET_MAX:
        errors.append(
            SettingsError(
                "daily_kcal_target",
                f"Цель по ккал — от {KCAL_TARGET_MIN} до {KCAL_TARGET_MAX}",
            )
        )
    protein = settings.daily_protein_target
    if protein is not None and not PROTEIN_TARGET_MIN <= protein <= PROTEIN_TARGET_MAX:
        errors.append(
            SettingsError(
                "daily_protein_target",
                f"Цель по белку — от {PROTEIN_TARGET_MIN} до {PROTEIN_TARGET_MAX} г",
            )
        )
    skipped = settings.skipped_cook_date
    # Дни готовки проверяются только у валидного расписания (not errors).
    if (
        skipped is not None
        and not errors
        and (
            skipped.isoweekday() not in cook_weekdays(settings)
            or skipped < settings.meal_anchor_date
        )
    ):
        errors.append(
            SettingsError(
                "skipped_cook_date",
                "Пропустить можно только день готовки после начала цикла",
            )
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
