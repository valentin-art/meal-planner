"""Печатает справочник рецептов — снапшот tests/recipes_snapshot.json (6.7).

С JSX сверяются только неизменные поля (tests/test_recipes_data.py); данные
целиком — с этим снапшотом, чтобы правка рецепта была видна в diff.

Запуск: uv run python tools/dump_recipes.py > tests/recipes_snapshot.json
"""

import json
from dataclasses import asdict

from meal_planner.core.recipes_data import ALL_RECIPES


def build() -> dict:
    # Через JSON: кортежи → списки, как при чтении снапшота.
    return json.loads(
        json.dumps(
            {rid: asdict(r) for rid, r in ALL_RECIPES.items()}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=1))
