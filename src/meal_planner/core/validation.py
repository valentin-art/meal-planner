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
SEED_LIMIT = 2**31  # зерно перемешивания — 0..2³¹−1, влезает в INTEGER SQLite


@dataclass(frozen=True, slots=True)
class SettingsError:
    field: str
    message: str


def shuffles_valid(settings: Settings) -> bool:
    # Строки пишет только сервер (8.4a) — проверка ловит правку БД руками.
    cycles = [s.from_cycle for s in settings.cycle_shuffles]
    return (
        all(c >= 0 for c in cycles)
        and cycles == sorted(set(cycles))
        and all(
            s.seed is None or 0 <= s.seed < SEED_LIMIT for s in settings.cycle_shuffles
        )
    )


def portion_scales_valid(settings: Settings) -> bool:
    # Строки пишет только сервер (8.5) — проверка ловит правку БД руками.
    rows = settings.portion_scales
    dates = [r.from_date for r in rows]
    return dates == sorted(set(dates)) and all(
        r.kcal_target is None or KCAL_TARGET_MIN <= r.kcal_target <= KCAL_TARGET_MAX
        for r in rows
    )


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
    if not shuffles_valid(settings):
        errors.append(
            SettingsError("cycle_shuffles", "Повреждена история перемешивания цикла")
        )
    if not portion_scales_valid(settings):
        errors.append(
            SettingsError("portion_scales", "Повреждена история масштаба порций")
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
