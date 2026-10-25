"""Печатает OpenAPI-схему API — снапшот для генерации типов фронтенда.

Запуск: cd frontend && npm run gen:api (пишет frontend/openapi.json и
src/api-types.ts). Сервер не нужен.
"""

import json

from meal_planner.api.main import create_app

print(json.dumps(create_app().openapi(), ensure_ascii=False, indent=2))
