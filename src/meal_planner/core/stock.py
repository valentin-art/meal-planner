"""Остатки продуктов (8.2): перевод в единицы справочника.

Остатки хранит клиент (счётчики Home Assistant), приложение их не помнит:
они приходят с запросом, как today. Счётчик бывает в своей единице («0,25
цукини» в шт, масло в г), поэтому сначала — в единицу ингредиента, по
правилам ниже. Что не перевести, возвращается с причиной, а не роняет расчёт.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from meal_planner.core.catalog_actions import WEIGHT_UNITS
from meal_planner.core.models import Catalog, CatalogIngredient, ShoppingTrip
from meal_planner.core.shopping import format_qty

# Крупные единицы счётчиков → единицы справочника.
SCALE = {"кг": ("г", Decimal(1000)), "л": ("мл", Decimal(1000))}
SPOON_ML = Decimal(15)  # столовая ложка — 15 мл по определению


@dataclass(frozen=True, slots=True)
class StockLine:
    ingredient_id: int
    qty: float
    unit: str  # единица счётчика, не обязательно ингредиента
    # Сущность счётчика в HA: списание (8.3b) отдаёт новые значения по ней.
    entity_id: str | None = None


@dataclass(frozen=True, slots=True)
class StockIgnored:
    ingredient_id: int
    name: str | None  # None — такого ингредиента нет
    qty: float
    unit: str
    reason: str


@dataclass(frozen=True, slots=True)
class StockedTrip:
    """Ближайшая закупка за вычетом остатков и строки, которые не учтены."""

    trip: ShoppingTrip
    ignored: tuple[StockIgnored, ...]


def convert(ingredient: CatalogIngredient, qty: Decimal, unit: str) -> Decimal | None:
    """qty в unit → в единице ингредиента; None — не перевести."""
    if unit in SCALE:
        unit, factor = SCALE[unit]
        qty *= factor
    target = ingredient.unit
    if unit == target:
        return qty
    if unit == "шт" and ingredient.store_piece is not None:
        # Штука в магазине (6.7) — то же, что «≈ N шт» в списке.
        return qty * Decimal(str(ingredient.store_piece))
    if unit == "г" and target not in WEIGHT_UNITS and ingredient.grams_per_unit:
        return qty / Decimal(str(ingredient.grams_per_unit))
    if unit == "мл" and target == "ст.л.":
        return qty / SPOON_ML
    return None


def unconvertible_reason(ingredient: CatalogIngredient, unit: str) -> str:
    reason = f"не перевести {unit} в {ingredient.unit}"
    if unit == "шт":
        return f"{reason}: у ингредиента нет веса штуки в магазине"
    return reason


def stock_in_catalog_units(
    catalog: Catalog, lines: Iterable[StockLine]
) -> tuple[dict[str, Decimal], list[StockIgnored]]:
    """Остаток по названию ингредиента, в его единице, и непринятые строки.

    Несколько строк на один ингредиент складываются; ноль — не остаток.
    """
    by_id = {i.id: i for i in catalog.ingredients.values()}
    stock: dict[str, Decimal] = {}
    ignored: list[StockIgnored] = []
    for line in lines:
        ingredient = by_id.get(line.ingredient_id)

        def ignore(reason: str, line=line, ingredient=ingredient) -> None:
            ignored.append(
                StockIgnored(
                    line.ingredient_id,
                    ingredient and ingredient.name,
                    line.qty,
                    line.unit,
                    reason,
                )
            )

        if ingredient is None:
            ignore("нет такого ингредиента в справочнике")
            continue
        qty = Decimal(str(line.qty))
        if qty < 0:
            ignore(f"отрицательный остаток: {format_qty(line.qty)} {line.unit}")
            continue
        if qty == 0:
            continue
        converted = convert(ingredient, qty, line.unit)
        if converted is None:
            ignore(unconvertible_reason(ingredient, line.unit))
            continue
        stock[ingredient.name] = stock.get(ingredient.name, Decimal(0)) + converted
    return stock, ignored
