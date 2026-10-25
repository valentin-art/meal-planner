"""Ккал и белок рецепта — сумма по ингредиентам (6.9), а не число из JSX.

Значения на 100 г — USDA FoodData Central, SR Legacy (2018-04; в комментарии
fdc_id). Чешский греческий йогурт 10% в USDA нет — по этикетке Mevgal 10%.
Ингредиенты-«или» считаются по первому варианту. Количество переводится в
граммы через GRAMS_PER_UNIT (г и мл — 1:1).
"""

from collections.abc import Iterable, Mapping
from decimal import ROUND_HALF_UP, Decimal
from types import MappingProxyType

from meal_planner.core.models import Ingredient

# название ингредиента → (ккал, белок г) на 100 г
PER_100G: Mapping[str, tuple[float, float]] = MappingProxyType(
    {
        "яйца": (143, 12.56),  # 171287 Egg, whole, raw, fresh
        "масло сливочное": (717, 0.85),  # 173410 Butter, salted
        "хлеб цельнозерновой": (252, 12.45),  # 172688 Bread, whole-wheat
        "греческий йогурт": (133, 5.5),  # этикетка Mevgal 10 % (řecký 10 %)
        "гранола или орехи": (489, 13.67),  # 171646 Granola, homemade
        "авокадо": (160, 2.0),  # 171705 Avocados, raw
        "сок лимона": (22, 0.35),  # 167747 Lemon juice, raw
        "куриное филе": (120, 22.5),  # 171077 Chicken breast, meat only, raw
        "киноа": (368, 14.12),  # 168874 Quinoa, uncooked
        "брокколи": (34, 2.82),  # 170379 Broccoli, raw
        "цукини": (17, 1.21),  # 169291 Zucchini, includes skin, raw
        "оливковое масло": (884, 0),  # 171413 Oil, olive
        "говядина": (128, 21.75),  # 171206 Beef, chuck for stew, raw
        "гречка": (346, 11.73),  # 170685 Buckwheat groats, roasted, dry
        "лук репчатый": (40, 1.1),  # 170000 Onions, raw
        "морковь": (41, 0.93),  # 170393 Carrots, raw
        "масло растительное": (884, 0),  # 171025 Oil, sunflower
        "паста цельнозерновая": (352, 13.87),  # 169738 Pasta, whole-wheat, dry
        # 173709 Tuna, light, canned in water, drained solids
        "тунец консервированный или креветки варёные очищенные": (86, 19.44),
        "шпинат": (23, 2.86),  # 168462 Spinach, raw
        "черри-томаты": (18, 0.88),  # 170457 Tomatoes, red, ripe, raw
        "булгур": (342, 12.29),  # 170688 Bulgur, dry
        "болгарский перец": (26, 0.99),  # 170108 Peppers, sweet, red, raw
        "лосось": (208, 20.42),  # 175167 Salmon, Atlantic, farmed, raw
        "рис басмати": (365, 7.13),  # 168877 Rice, white, long-grain, raw
        # среднее 169249 Lettuce green leaf / 168462 Spinach / 169387 Arugula
        "листовой салат/шпинат/руккола": (21, 2.27),
        "огурец": (15, 0.65),  # 168409 Cucumber, with peel, raw
        "помидор": (18, 0.88),  # 170457 Tomatoes, red, ripe, raw
        "грецкие орехи": (654, 15.23),  # 170187 Walnuts, english
        "семечки подсолнечные": (584, 20.78),  # 170562 Sunflower seed kernels
        # 175180 Shrimp, cooked
        "креветки варёные очищенные (размороженные или замороженные)": (99, 23.98),
        "баклажан": (25, 0.98),  # 169228 Eggplant, raw
        "чеснок": (149, 6.36),  # 169230 Garlic, raw
        "маслины": (116, 0.84),  # 169094 Olives, ripe, canned
        "фета": (265, 14.21),  # 173420 Cheese, feta
        "нут консервированный": (139, 7.05),  # 173800 Chickpeas, canned, drained
        "пармезан": (392, 35.75),  # 170848 Cheese, parmesan, hard
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


def grams(ingredient: Ingredient) -> Decimal:
    if ingredient.unit in ("г", "мл"):
        per_unit = 1.0
    else:
        per_unit = GRAMS_PER_UNIT[(ingredient.name, ingredient.unit)]
    return Decimal(str(ingredient.qty)) * Decimal(str(per_unit))


def nutrition(ingredients: Iterable[Ingredient]) -> tuple[int, int]:
    """(ккал, белок г) порции: ккал — до 5, белок — до 1 (как в рецептах JSX)."""
    kcal = protein = Decimal(0)
    for ingredient in ingredients:
        per_kcal, per_protein = PER_100G[ingredient.name]
        g = grams(ingredient)
        kcal += g * Decimal(str(per_kcal)) / 100
        protein += g * Decimal(str(per_protein)) / 100
    return (
        int((kcal / 5).quantize(Decimal(1), ROUND_HALF_UP)) * 5,
        int(protein.quantize(Decimal(1), ROUND_HALF_UP)),
    )
