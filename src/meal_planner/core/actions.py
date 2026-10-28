"""Атомарные действия над настройками: что каждое действие меняет.

Чистые функции Settings → Settings; HTTP-слой (и любой другой клиент) только
вызывает их и сохраняет результат. Как в JS persist({...settings, поле}):
меняются только поля действия, остальные (включая якорь) остаются.
"""

from dataclasses import replace
from datetime import date

from meal_planner.core.models import (
    Catalog,
    CycleShuffle,
    PortionScale,
    Settings,
    ShoppingTiming,
)
from meal_planner.core.scheduling import (
    cook_weekdays,
    first_free_cycle,
    first_free_day,
    settle_skip,
    today_position,
    trip_cook_date,
    without_skip,
)

WEEKDAY_SHORT = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


class ActionError(ValueError):
    """Действие невыполнимо при текущих настройках (API отвечает 422)."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field
        self.message = message


def fmt_day(d: date) -> str:
    return f"{WEEKDAY_SHORT[d.weekday()]} {d:%d.%m}"


def set_anchor_date(settings: Settings, meal_anchor_date: date) -> Settings:
    # Любая дата, прошлая или будущая — как в JS. Пустую дату (в JS очищенный
    # <input type="date"> сохранял "" и ломал расписание) отсекает API.
    # Явный якорь задаёт цикл заново — пропуск снимается (7.7).
    return replace(settings, meal_anchor_date=meal_anchor_date, skipped_cook_date=None)


def set_cook_schedule(
    settings: Settings,
    today: date,
    cook_count: int,
    cook_weekday1: int,
    cook_weekday2: int | None = None,
) -> Settings:
    # cook_weekday2=None — оставить сохранённый: в JS второй день переживает
    # переключение 1 ↔ 2 готовки и восстанавливается при возврате к двум.
    # С новыми днями пропущенной готовки может не быть — пропуск снимается;
    # начатая пауза засчитывается, цикл продолжается с того же блюда (7.7).
    settings = settle_skip(settings, today)
    if settings.skipped_cook_date is not None:
        if settings.skipped_cook_date < today:
            settings = without_skip(settings)
        else:
            settings = replace(settings, skipped_cook_date=None)
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


def set_nutrition_target(
    settings: Settings,
    today: date,
    daily_kcal_target: int | None,
    daily_protein_target: int | None,
) -> Settings:
    # Обе цели целиком: форма шлёт оба поля, None — цели нет (8.1).
    # Масштаб порций (8.5) следует за целью — с первой некупленной партии.
    if portions_target(settings) is not None:
        settings = with_portion_scale(settings, today, daily_kcal_target)
    return replace(
        settings,
        daily_kcal_target=daily_kcal_target,
        daily_protein_target=daily_protein_target,
    )


def portions_target(settings: Settings) -> int | None:
    """Под какую цель масштабируются порции сейчас; None — масштаба нет."""
    rows = settings.portion_scales
    return rows[-1].kcal_target if rows else None


def with_portion_scale(settings: Settings, today: date, target: int | None) -> Settings:
    """С первой некупленной партии — порции под target (8.5); None — без масштаба.

    Строки с той же даты и позже заменяются; то же состояние — без новой строки.
    """
    settings = settle_skip(settings, today)
    if portions_target(settings) == target:
        return settings
    first = first_free_day(settings, today)
    kept = tuple(r for r in settings.portion_scales if r.from_date < first)
    if (kept[-1].kcal_target if kept else None) == target:
        # Отмена ещё не наступившей правки — возврат к тому, что действует.
        return replace(settings, portion_scales=kept)
    return replace(settings, portion_scales=(*kept, PortionScale(first, target)))


def set_portion_scaling(settings: Settings, today: date, enabled: bool) -> Settings:
    """Подгонять порции обедов и ужинов под цель по ккал (8.5) или перестать."""
    if enabled and settings.daily_kcal_target is None:
        raise ActionError("enabled", "Сначала задайте цель по ккал")
    return with_portion_scale(
        settings, today, settings.daily_kcal_target if enabled else None
    )


def set_cycle_position(
    catalog: Catalog, settings: Settings, today: date, dish: int
) -> Settings:
    """«Сегодня — блюдо №dish» (0 — сегодня день 0): сервер сам выбирает якорь."""
    # Каталог нужен: какие блюда достижимы, зависит от рыбы в слотах (6.6).
    options = today_position(
        catalog,
        today,
        settings.meal_anchor_date,
        cook_weekdays(settings),
        shuffles=settings.cycle_shuffles,
    ).options
    anchor = next((o.anchor for o in options if o.dish == dish), None)
    if anchor is None:
        possible = ", ".join(
            "день 0" if o.dish == 0 else f"блюдо №{o.dish}" for o in options
        )
        raise ActionError("dish", f"Сегодня может быть только: {possible}")
    # Позиция задана явно — пропуск снимается (из паузы так возвращаются раньше).
    return replace(settings, meal_anchor_date=anchor, skipped_cook_date=None)


def skip_next_cook_error(settings: Settings, today: date) -> str | None:
    """Почему нельзя пропустить ближайшую готовку; None — можно. settings —
    после settle_skip."""
    if settings.skipped_cook_date is not None:
        return (
            f"Уже пропущена готовка {fmt_day(settings.skipped_cook_date)}"
            " — сначала отмените этот пропуск"
        )
    if trip_cook_date(settings, today) < settings.meal_anchor_date:
        return "Цикл ещё не начался — первую готовку сдвигают первым днём цикла"
    return None


def skip_next_cook(settings: Settings, today: date) -> Settings:
    """Пропустить готовку, к которой ближайшая закупка: её окно — пауза (7.7)."""
    settings = settle_skip(settings, today)
    error = skip_next_cook_error(settings, today)
    if error:
        raise ActionError("skipped_cook_date", error)
    return replace(settings, skipped_cook_date=trip_cook_date(settings, today))


def unskip_cook(settings: Settings, today: date) -> Settings:
    settings = settle_skip(settings, today)
    skipped = settings.skipped_cook_date
    if skipped is None:
        raise ActionError("skipped_cook_date", "Пропущенной готовки нет")
    if skipped < today:
        raise ActionError(
            "skipped_cook_date",
            "Пауза уже идёт — чтобы вернуться к циклу раньше, выберите в"
            " настройках, какое блюдо сегодня",
        )
    return replace(settings, skipped_cook_date=None)


def with_cycle_shuffle(settings: Settings, today: date, seed: int | None) -> Settings:
    """С первого некупленного цикла — новый порядок (8.4a); до него прежний.

    Строки с того же цикла и позже заменяются; ранние нужны цепочке.
    """
    settings = settle_skip(settings, today)
    first = first_free_cycle(settings, today)
    kept = tuple(s for s in settings.cycle_shuffles if s.from_cycle < first)
    return replace(settings, cycle_shuffles=(*kept, CycleShuffle(first, seed)))


def reshuffle_cycle(settings: Settings, today: date, seed: int) -> Settings:
    # Зерно приходит снаружи (API): core остаётся детерминированным.
    return with_cycle_shuffle(settings, today, seed)


def reset_cycle_order(settings: Settings, today: date) -> Settings:
    shuffles = settings.cycle_shuffles
    if not shuffles or shuffles[-1].seed is None:
        raise ActionError("cycle_shuffles", "Порядок и так по слотам 1→5")
    return with_cycle_shuffle(settings, today, None)
