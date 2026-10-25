# meal-planner

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
