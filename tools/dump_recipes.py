"""Печатает справочник рецептов — снапшот tests/recipes_snapshot.json (6.7).

С JSX сверяются только неизменные поля (tests/test_recipes_data.py); данные
целиком — с этим снапшотом, чтобы правка рецепта была видна в diff.

Запуск: uv run python tools/dump_recipes.py > tests/recipes_snapshot.json
"""

import json
from dataclasses import asdict

from meal_planner.core.models import Recipe
from meal_planner.core.seed import SEED_CATALOG


def recipe_data(recipe: Recipe) -> dict:
    # scale — множитель порции в расписании (8.5), не данные рецепта.
    return {k: v for k, v in asdict(recipe).items() if k != "scale"}


def build() -> dict:
    # Через JSON: кортежи → списки, как при чтении снапшота.
    return json.loads(
        json.dumps(
            {rid: recipe_data(r) for rid, r in SEED_CATALOG.recipes.items()},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=1))
