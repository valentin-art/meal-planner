# ARCHITECTURE.md

Техническое описание системы. Обоснование решений и пошаговый план —
в `implementation-plan.md`. Здесь — как система устроена, для быстрой
ориентации без перечитывания всего плана.

## Схема потока данных

```
┌─────────────┐     intent: POST actions/set-cook-schedule     ┌──────────────┐
│   Frontend   │ ─────────────────────────────────────────────▶ │  API-слой    │
│ (React, чисто│                                                 │  (FastAPI)   │
│ презентация) │ ◀───────────────────────────────────────────── │              │
└─────────────┘        полный пересчитанный AppState (JSON)     └──────┬───────┘
                                                                        │
                                                          читает/пишет  │ вызывает
                                                                        ▼
                                                                 ┌──────────────┐
                                                                 │ core/planner │
                                                                 │  (чистый     │
                                                                 │   Python)    │
                                                                 └──────┬───────┘
                                                                        │
                                                       ┌────────────────┼───────────────┐
                                                       ▼                ▼               ▼
                                          scheduling.py  shopping.py  catalog.py  (seed.py ← recipes_data.py)
                                                                        │
                                                                        ▼
                                                                 ┌──────────────┐
                                                                 │   SQLite     │
                                                                 │ settings +   │
                                                                 │  каталог     │
                                                                 └──────────────┘
```

Ключевой инвариант: **любое действие пользователя = новый `AppState` целиком**.
Frontend не патчит локальный state точечно — он получает готовый ответ и
заменяет им всё, что относится к плану.

## Раскладка репозитория

```
src/meal_planner/{core,db,api}/   # один Python-пакет, управляется uv
tests/                            # pytest, включая test_architecture.py
frontend/                         # Vite + React, base './', сборка в dist/
ha/packages/                      # YAML-пакет Home Assistant (монтируется в HA)
```

Запуск: `uv run meal-planner` (uvicorn на `0.0.0.0:$MEALPLAN_PORT`, так же
стартует образ), для разработки — `uv run uvicorn meal_planner.api.main:app
--reload`. Конфигурация — через
`MEALPLAN_DB_PATH`, `MEALPLAN_STATIC_DIR`, `MEALPLAN_PORT`.

## Слои и их границы ответственности

| Слой | Отвечает за | НЕ отвечает за |
|---|---|---|
| `src/meal_planner/core/models.py` | Формы данных (Recipe, Settings, DayMeals, ShoppingItem, AppState) | Валидация HTTP-входа, персистентность |
| `src/meal_planner/core/recipes_data.py` | Исходные рецепты — сид каталога (фаза 5) | Живые данные (они в БД), ротация |
| `src/meal_planner/core/catalog.py` | `build_catalog`: рецепты с ккал по справочнику, слоты | Откуда каталог (БД или сид) |
| `src/meal_planner/core/catalog_actions.py` | Правила правки рецептов, слотов и справочника | Запись в БД, HTTP |
| `src/meal_planner/core/seed.py` | Сид-каталог из констант (`SEED_CATALOG`) — для миграции и golden | Живые данные |
| `src/meal_planner/core/scheduling.py` | Дата → какие блюда, даты готовки/закупки | Агрегация ингредиентов, HTTP |
| `src/meal_planner/core/shopping.py` | Список рецептов → агрегированный список покупок | Откуда взялись рецепты (даты) |
| `src/meal_planner/core/planner.py` | Оркестрация: settings → полный AppState | HTTP, SQLite-детали |
| `src/meal_planner/db/repository.py` | Чтение/запись `settings` и каталога в SQLite | Бизнес-логика, пересчёт |
| `src/meal_planner/db/schema.py` | Схема и миграции по `PRAGMA user_version`, сид каталога | Правила каталога |
| `src/meal_planner/db/backup.py` | `meal-planner-backup`: согласованная копия БД, ротация | Расписание запуска (cron на хосте) |
| `src/meal_planner/api/main.py` | HTTP-контракт, вызов planner, сериализация | Бизнес-логика (только вызывает `core/`) |
| `frontend/` | Рендер `AppState`, отправка intent-запросов | Любые вычисления над датами/рецептами |

Правило: если код нужно тестировать через HTTP-запрос — он не в том слое.
Вся логика в `core/` должна быть проверяема прямым вызовом функций.

Зависимости направлены только вниз: `api → db, core`; `db → core.models`;
`core → ничего из проекта выше`. Это проверяется `tests/test_architecture.py`
(в `core/` нет импортов `fastapi`, `pydantic`, `sqlite3`, `meal_planner.api`,
`meal_planner.db`).

## Модель состояния

`AppState` — единственный объект, который видит frontend. Он не хранится
целиком в БД (см. `implementation-plan.md` → "Делать": расписание не
кэшируется) — строится заново при каждом запросе из:

```
AppState = f(Catalog, Settings, today, window_days)
```

Это делает `AppState` чистой функцией от каталога, settings и текущей даты — воспроизводим,
без скрытого состояния, легко тестируется.

## API-контракт (сводно)

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/state?from=&to=` | Полный `AppState` для окна дат |
| GET | `/api/recipes` | Справочник рецептов (из БД, со статусом) |
| GET | `/api/ingredients` | Справочник ингредиентов, единицы и категории для формы |
| POST | `/api/recipes/preview` | Ккал и белок черновика рецепта — только чтение |
| GET | `/api/summary` | Сегодня, завтра и ближайшая закупка — компактно, для HA |
| GET | `/api/cookbook` | Ближайшая готовка и активные рецепты по видам — для вкладки «Рецепты» HA (9.1), только чтение; с 10.3 и `cooking` — готовка каждого рецепта вне очереди; с 10.6 `now` и `cooking_now` — текущая партия и завтрак (поток партии) |
| POST | `/api/cookbook` | То же по остаткам из счётчиков HA (тело как у `/api/shopping/remaining`): у ингредиентов `have`/`short`, строки нехватки для подтверждения (10.3); только чтение |
| POST | `/api/shopping/remaining` | Ближайшая закупка за вычетом остатков из HA (8.2) — только чтение, остатки не хранятся |
| GET | `/api/health` | Живость для `HEALTHCHECK` (только чтение БД) |
| POST | `/api/actions/{action_name}` | Атомарное действие (не generic PATCH — см. ниже); с фазы 5 и каталог: `save-recipe`, `archive-recipe`, `restore-recipe`, `set-cycle-slot`, `save-ingredient`; с 8.3b — `mark-cooked`, `unmark-cooked`, `set-dish-portions` (отметка «приготовлено» и новые значения счётчиков HA); с 10.3 — `use-ingredient`, `unuse-ingredient`, `cook-recipe` (`allow_missing`), `uncook-recipe`, `set-recipe-portions` — готовка любого рецепта по журналу списаний, отмена возвращает списанное; поле `scope` (10.6): `plan` — партия и завтрак, `extra` — вне очереди |

### Почему action-эндпоинты, а не `PATCH /api/settings`

Обсуждалось и зафиксировано в `implementation-plan.md`. Коротко: атомарное
действие (`skip-next-cook`, `set-cook-day`) — это единица, которую легко:
- протестировать изолированно,
- описать как tool для голосового ассистента (см. раздел про Home Assistant
  в `implementation-plan.md` — не реализуется сейчас, но эндпоинты
  проектируются с этим в уме),
- валидировать (входные параметры конкретного действия, а не произвольный
  diff всей структуры `Settings`).

## Развёртывание и Home Assistant (фаза 7)

```
Хост HA (Docker, HA Container в network_mode: host)
├── homeassistant   → hass_ingress: UI в боковой панели под логином HA;
│                     YAML-пакет: rest-сенсоры, rest_command, скрипты для Assist
└── meal-planner    → FastAPI + core + SQLite + фронтенд,
                      порт только на 127.0.0.1, БД в каталоге хоста
```

Что из этого влияет на код:
- относительные URL во фронтенде и `base: './'` (`hass_ingress` отдаёт
  приложение под префиксом `/api/ingress/<name>/`);
- весь конфиг через переменные окружения;
- `GET /api/health` для `HEALTHCHECK` (watchdog'а Supervisor'а нет);
- `Dockerfile` на `uv sync --frozen`, образ под `linux/amd64` и `linux/arm64`.

Compose и решения — `implementation-plan.md`, «Home Assistant integration».

## Персистентность

Единственная таблица — `settings` (singleton, одна строка). Всё остальное
(`schedule`, `shopping list`) — производные, вычисляются на лету в
`planner.recompute()`. Причина: пересчёт дешёвый (арифметика над датами),
а кэш создаёт риск рассинхрона между сохранённым и актуальным состоянием.

```sql
CREATE TABLE settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    meal_anchor_date TEXT NOT NULL,
    cook_count INTEGER NOT NULL,
    cook_weekday1 INTEGER NOT NULL,
    cook_weekday2 INTEGER,
    shopping_timing TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    skipped_cook_date TEXT,         -- пропущенная готовка (7.7), NULL — нет
    daily_kcal_target INTEGER,      -- суточная цель (8.1), NULL — нет
    daily_protein_target INTEGER
);

-- 8.3b: отметки «приготовлено» — день готовки партии или день завтрака.
CREATE TABLE cooked (
    day TEXT NOT NULL,
    recipe_id TEXT NOT NULL REFERENCES recipes (id),
    PRIMARY KEY (day, recipe_id)
);
```

## Соответствие оригинальной JS-логике

Порт должен быть побитово эквивалентен текущему `MealPlanApp.tsx` по
бизнес-правилам (не по коду). Таблица соответствия функций — в
`implementation-plan.md`, раздел 3. Golden-тесты — обязательное условие
до переноса (см. `CLAUDE.md`).

## Что специально оставлено простым (не переусложнять)

- Нет ORM — одна таблица, `sqlite3` из стандартной библиотеки достаточно.
- Нет WebSocket/SSE — синхронный request/response покрывает однопользовательский
  сценарий полностью (см. `implementation-plan.md` раздел 0).
- Нет очереди задач/фоновых воркеров — пересчёт синхронный и быстрый,
  асинхронная обработка не нужна.
