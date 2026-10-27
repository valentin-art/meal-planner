"""Ккал и белок рецепта — сумма по ингредиентам (6.9), а не число из JSX.

Значения на 100 г — USDA FoodData Central, SR Legacy (2018-04; источник —
«fdc <fdc_id> <описание>», с 5.2 он попадает в справочник ингредиентов в БД).
Чешский греческий йогурт 10% в USDA нет — по этикетке Mevgal 10%.
Ингредиенты-«или» считаются по первому варианту. Количество переводится в
граммы через вес единицы (г и мл — 1:1).

PER_100G и GRAMS_PER_UNIT — сид справочника (core/seed.py); считают
nutrition/grams по справочнику из каталога (5.3).
"""

from collections.abc import Iterable, Mapping
from decimal import ROUND_HALF_UP, Decimal
from types import MappingProxyType

from meal_planner.core.models import CatalogIngredient, Ingredient

# название ингредиента → (ккал, белок г, источник) на 100 г
PER_100G: Mapping[str, tuple[float, float, str]] = MappingProxyType(
    {
        "яйца": (143, 12.56, "fdc 171287 Egg, whole, raw, fresh"),
        "масло сливочное": (717, 0.85, "fdc 173410 Butter, salted"),
        "хлеб цельнозерновой": (252, 12.45, "fdc 172688 Bread, whole-wheat"),
        "греческий йогурт": (133, 5.5, "этикетка Mevgal 10 % (řecký 10 %)"),
        "гранола или орехи": (489, 13.67, "fdc 171646 Granola, homemade"),
        "авокадо": (160, 2.0, "fdc 171705 Avocados, raw"),
        "сок лимона": (22, 0.35, "fdc 167747 Lemon juice, raw"),
        "куриное филе": (120, 22.5, "fdc 171077 Chicken breast, meat only, raw"),
        "киноа": (368, 14.12, "fdc 168874 Quinoa, uncooked"),
        "брокколи": (34, 2.82, "fdc 170379 Broccoli, raw"),
        "цукини": (17, 1.21, "fdc 169291 Zucchini, includes skin, raw"),
        "оливковое масло": (884, 0, "fdc 171413 Oil, olive"),
        "говядина": (128, 21.75, "fdc 171206 Beef, chuck for stew, raw"),
        "гречка": (346, 11.73, "fdc 170685 Buckwheat groats, roasted, dry"),
        "лук репчатый": (40, 1.1, "fdc 170000 Onions, raw"),
        "морковь": (41, 0.93, "fdc 170393 Carrots, raw"),
        "масло растительное": (884, 0, "fdc 171025 Oil, sunflower"),
        "паста цельнозерновая": (352, 13.87, "fdc 169738 Pasta, whole-wheat, dry"),
        "тунец консервированный или креветки варёные очищенные": (
            86,
            19.44,
            "fdc 173709 Tuna, light, canned in water, drained solids",
        ),
        "шпинат": (23, 2.86, "fdc 168462 Spinach, raw"),
        "черри-томаты": (18, 0.88, "fdc 170457 Tomatoes, red, ripe, raw"),
        "булгур": (342, 12.29, "fdc 170688 Bulgur, dry"),
        "болгарский перец": (26, 0.99, "fdc 170108 Peppers, sweet, red, raw"),
        "лосось": (208, 20.42, "fdc 175167 Salmon, Atlantic, farmed, raw"),
        "рис басмати": (365, 7.13, "fdc 168877 Rice, white, long-grain, raw"),
        "листовой салат/шпинат/руккола": (
            21,
            2.27,
            "среднее fdc 169249 Lettuce green leaf / 168462 Spinach / 169387 Arugula",
        ),
        "огурец": (15, 0.65, "fdc 168409 Cucumber, with peel, raw"),
        "помидор": (18, 0.88, "fdc 170457 Tomatoes, red, ripe, raw"),
        "грецкие орехи": (654, 15.23, "fdc 170187 Walnuts, english"),
        "семечки подсолнечные": (584, 20.78, "fdc 170562 Sunflower seed kernels"),
        "креветки варёные очищенные (размороженные или замороженные)": (
            99,
            23.98,
            "fdc 175180 Shrimp, cooked",
        ),
        "баклажан": (25, 0.98, "fdc 169228 Eggplant, raw"),
        "чеснок": (149, 6.36, "fdc 169230 Garlic, raw"),
        "маслины": (116, 0.84, "fdc 169094 Olives, ripe, canned"),
        "фета": (265, 14.21, "fdc 173420 Cheese, feta"),
        "нут консервированный": (139, 7.05, "fdc 173800 Chickpeas, canned, drained"),
        "пармезан": (392, 35.75, "fdc 170848 Cheese, parmesan, hard"),
    }
)

# Сколько граммов в единице, если единица — не г/мл (USDA food_portion).
GRAMS_PER_UNIT: Mapping[tuple[str, str], float] = MappingProxyType(
    {
        ("яйца", "шт"): 50,  # 1 large
        ("масло сливочное", "ст.л."): 14.2,  # 1 tbsp
        ("оливковое масло", "ст.л."): 13.5,  # 1 tbsp
        ("масло растительное", "ст.л."): 13.6,  # 1 tbsp
        ("чеснок", "зубчик"): 3,  # 1 clove
    }
)


def grams(ingredients: Mapping[str, CatalogIngredient], line: Ingredient) -> Decimal:
    if line.unit in ("г", "мл"):
        per_unit = 1.0
    else:
        per_unit = ingredients[line.name].grams_per_unit
        if per_unit is None:
            raise ValueError(f"{line.name}: не задан вес единицы «{line.unit}»")
    return Decimal(str(line.qty)) * Decimal(str(per_unit))


def nutrition(
    ingredients: Mapping[str, CatalogIngredient], lines: Iterable[Ingredient]
) -> tuple[int, int]:
    """(ккал, белок г) порции: ккал — до 5, белок — до 1 (как в рецептах JSX)."""
    kcal = protein = Decimal(0)
    for line in lines:
        ingredient = ingredients[line.name]
        g = grams(ingredients, line)
        kcal += g * Decimal(str(ingredient.kcal_100g)) / 100
        protein += g * Decimal(str(ingredient.protein_100g)) / 100
    return (
        int((kcal / 5).quantize(Decimal(1), ROUND_HALF_UP)) * 5,
        int(protein.quantize(Decimal(1), ROUND_HALF_UP)),
    )
