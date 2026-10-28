"""Перевод количества в строке рецепта в единицу справочника (8.6b).

Рецепт от Assist модель пишет в привычных единицах («1 морковь», «стакан
бульона», «½ ч.л. паприки»), а рецепт хранит количество в единице
ингредиента — правило 6.7: г — твёрдое, мл — жидкое, ч.л./ст.л. — специи,
масла, соусы. Остатки HA переводит core/stock.py по своим правилам.
"""

from decimal import ROUND_HALF_UP, Decimal

from meal_planner.core.models import CatalogIngredient

# Крупные и бытовые единицы → единица справочника и множитель.
SCALE = {
    "кг": ("г", Decimal(1000)),
    "л": ("мл", Decimal(1000)),
    "стакан": ("мл", Decimal(250)),
}
# Объём ложки в мл — по определению; вес ложки у каждого ингредиента свой.
VOLUME_ML = {"мл": Decimal(1), "ч.л.": Decimal(5), "ст.л.": Decimal(15)}
LINE_UNITS = ("г", "кг", "мл", "л", "стакан", "ч.л.", "ст.л.", "шт", "зубчик")
# До целого — г и мл; ложки и штуки — до четверти.
WHOLE = ("г", "мл")
QUARTER = Decimal("0.25")


def to_ingredient_unit(
    ingredient: CatalogIngredient, qty: Decimal, unit: str
) -> Decimal | None:
    """qty в unit → в единице ингредиента; None — не перевести."""
    if unit in SCALE:
        unit, factor = SCALE[unit]
        qty *= factor
    target = ingredient.unit
    if unit == target:
        return qty
    if unit == "шт":
        # Штука в магазине (6.7): «1 цукини» — его вес в единице справочника.
        if ingredient.store_piece is None:
            return None
        return qty * Decimal(str(ingredient.store_piece))
    # г и мл — 1:1, как в nutrition.grams.
    if unit in ("г", "мл") and target in ("г", "мл"):
        return qty
    if unit in VOLUME_ML and target in VOLUME_ML:
        return qty * VOLUME_ML[unit] / VOLUME_ML[target]
    if unit == "г" and ingredient.grams_per_unit:
        # Ложка, штука, зубчик — по весу единицы ингредиента.
        return qty / Decimal(str(ingredient.grams_per_unit))
    return None


def rounded(unit: str, qty: Decimal) -> Decimal:
    """г и мл — до целого, остальное — до ¼; половина вверх."""
    if unit in WHOLE:
        return qty.quantize(Decimal(1), ROUND_HALF_UP)
    return (qty / QUARTER).quantize(Decimal(1), ROUND_HALF_UP) * QUARTER


def unconvertible_line(name: str, unit: str, target: str) -> str:
    if unit not in LINE_UNITS:
        return f"«{name}»: единица — одна из: {', '.join(LINE_UNITS)}"
    reason = f"«{name}»: не перевести {unit} в {target}"
    if unit == "шт":
        reason += " — у ингредиента нет веса штуки"
    return f"{reason} — укажите в {target}"
