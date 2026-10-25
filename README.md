# meal-planner

## Frontend

`frontend/` — Vite + React, Node 24 (`frontend/.node-version`; например через
[fnm](https://github.com/Schniz/fnm) с `--use-on-cd`).

```sh
uv run uvicorn meal_planner.api.main:app --reload   # backend на :8000
cd frontend
npm ci
npm run dev        # Vite, /api проксируется на :8000
npm test           # vitest
npm run build      # frontend/dist — его раздаёт FastAPI (MEALPLAN_STATIC_DIR)
```

Типы API (`src/api-types.ts`) генерируются из OpenAPI-схемы backend. После
изменения схем в `api/schemas.py` — `npm run gen:api` (обновляет
`frontend/openapi.json` и типы); `uv run pytest` падает, пока снапшот устарел.

## Docker

```sh
docker build -t meal-planner .
docker run -d -p 8000:8000 -v meal-planner-data:/data -e TZ=Europe/Berlin meal-planner
```

| Переменная | Назначение | В образе |
|---|---|---|
| `MEALPLAN_DB_PATH` | файл SQLite (на томе `/data`) | `/data/mealplan.db` |
| `MEALPLAN_STATIC_DIR` | собранный фронтенд | `/app/frontend/dist` |
| `MEALPLAN_PORT` | порт uvicorn | `8000` |
| `TZ` | таймзона для «сегодня» | UTC |

Сборка под две архитектуры:

```sh
docker buildx build --platform linux/amd64,linux/arm64 -t meal-planner .
```
