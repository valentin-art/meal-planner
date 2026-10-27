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

`src/ui-snapshots.test.tsx` снимает текст всех вкладок на ответах backend из
`src/__fixtures__/ui-states.json` в `src/__snapshots__/` (построчно, файл в
git). Если меняется то, что отдаёт `/api/state`, — `npm run gen:fixtures`
(pytest подскажет). Сознательное изменение UI — `npx vitest run -u` в том же
коммите; diff `.snap` показывает, что поменялось на экране.

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

## Развёртывание рядом с Home Assistant

Как устроено и почему так — `implementation-plan.md`, «Home Assistant
integration». Здесь только порядок действий. `<user>` — пользователь на хосте
HA, пути `/opt/...` — пример раскладки (compose стека и конфиг HA лежат в
`/opt`).

```
Хост (Docker)
├── homeassistant   network_mode: host
│     ├── hass_ingress: панель «Питание» → http://127.0.0.1:8001 (под логином HA)
│     └── packages/meal_planner ← ~/meal-planner/ha/packages (:ro)
│           сенсоры, rest_command, скрипты для Assist, фразы, автоматизация
└── meal-planner    127.0.0.1:8001 → :8000, БД в /opt/meal-planner-data
```

Порт открыт только на loopback: из сети API не виден, аутентификация не
нужна. HA (в host-сети) ходит на `127.0.0.1:8001`.

### Первая установка

Порядок важен: клон — раньше, чем `docker compose up` для HA (см.
«Неполадки»).

1. Клон репо вне `/opt` (там свой git конфигов). Репо приватный — deploy key
   только на чтение:

   ```sh
   ssh-keygen -t ed25519 -f ~/.ssh/meal_planner_deploy -N '' -C meal-planner@ha
   cat ~/.ssh/meal_planner_deploy.pub   # → GitHub: Settings → Deploy keys, без write
   cat >> ~/.ssh/config <<'EOF'
   Host github-meal-planner
     HostName github.com
     User git
     IdentityFile ~/.ssh/meal_planner_deploy
     IdentitiesOnly yes
   EOF
   git clone github-meal-planner:valentin-art/meal-planner.git ~/meal-planner
   ```

2. Каталог данных. Приложение в контейнере работает как uid 10001 — он
   владелец; группа — ваша (чтение копий через sshfs), setgid — чтобы новые
   файлы наследовали группу; писать может только контейнер:

   ```sh
   sudo install -d -o 10001 -g "$(id -g)" -m 2750 /opt/meal-planner-data
   ```

3. Сервис в `/opt/docker-compose.yaml`:

   ```yaml
     meal-planner:
       container_name: meal-planner
       build: /home/<user>/meal-planner
       image: meal-planner:local
       restart: unless-stopped
       ports:
         - "127.0.0.1:8001:8000"
       volumes:
         - /opt/meal-planner-data:/data
         - /etc/localtime:/etc/localtime:ro   # «сегодня» в той же зоне, что у HA
   ```

   и в `volumes:` сервиса `homeassistant`:

   ```yaml
         - /home/<user>/meal-planner/ha/packages:/config/packages/meal_planner:ro
   ```

4. `configuration.yaml` HA:

   ```yaml
   homeassistant:
     packages: !include_dir_named packages

   ingress:
     meal_planner:
       title: Питание
       icon: mdi:food
       work_mode: ingress
       ui_mode: toolbar   # шапка с ☰: без неё в iOS Companion не выйти из панели
       url: http://127.0.0.1:8001
   ```

5. HACS → ⋮ → Custom repositories → `lovelylain/hass_ingress` (Integration)
   → Download.

6. Запуск и проверка:

   ```sh
   cd /opt && docker compose up -d --build meal-planner && docker compose up -d homeassistant
   docker ps --filter name=meal-planner          # (healthy)
   curl -s 127.0.0.1:8001/api/health             # {"status":"ok"}
   docker exec homeassistant ls /config/packages/meal_planner   # meal_planner.yaml
   ```

7. В HA:
   - «Устройства и службы → Добавить → Локальный список дел» «Покупки»
     (`todo.pokupki` — список по умолчанию для покупок);
   - «Голосовые ассистенты → Открыть доступ»: сенсоры и скрипты
     `meal_planner_*` (хранится в `.storage`, из YAML не задаётся);
   - в пайплайне Assist — «Предпочитать локальную обработку команд»
     (частые вопросы отвечаются без LLM).

8. Бэкап по cron (`crontab -e`, пользователь в группе `docker`):

   ```
   30 3 * * * docker exec meal-planner meal-planner-backup 2>&1 | logger -t meal-planner-backup
   ```

### Обновление

```sh
docker exec meal-planner meal-planner-backup   # копия до обновления — точка отката
cd ~/meal-planner && git pull
```

| Что поменялось | Что сделать |
|---|---|
| приложение (`src/`, `frontend/`) | `cd /opt && docker compose up -d --build meal-planner` |
| скрипты / автоматизации в `ha/packages` | HA: «Инструменты разработчика → YAML» → проверить → перезагрузить «Скрипты», «Автоматизации» |
| сенсоры, `rest_command`, фразы в `ha/packages` | `docker restart homeassistant` |

Миграции БД выполняются сами — по `PRAGMA user_version`, при первом запросе
после старта (его делает уже healthcheck). Откатить образ после миграции
нельзя: старое приложение не открывает БД новой схемы (`SchemaTooNewError`,
HTTP 500, `unhealthy`). Откат — прежний образ **и** копия БД, снятая до
обновления (восстановление — ниже).

### Бэкап и восстановление

`meal-planner-backup` снимает согласованную копию (backup API SQLite,
проверка `integrity_check`) в `/opt/meal-planner-data/backups`, хранит
последние 14 (`--keep N`). Лог — `journalctl -t meal-planner-backup`. Копии на
том же диске: от неудачного действия и порчи файла спасают, от потери диска —
нет.

В копии — вся БД: настройки **и каталог** — рецепты (в том числе архивные),
справочник ингредиентов, состав цикла. С фазы 5 рецепты правятся в
приложении, и эти правки есть только в БД: в репо остался лишь исходный
сид, кроме бэкапа их взять неоткуда.

Восстановление последней копии:

```sh
cd /opt && docker compose stop meal-planner
sudo sh -c 'cd /opt/meal-planner-data && cp mealplan.db mealplan.db.before-restore \
  && cp "$(ls -1 backups/mealplan-*.db | tail -1)" mealplan.db \
  && rm -f mealplan.db-journal && chown 10001 mealplan.db* && chmod 640 mealplan.db*'
docker compose start meal-planner
```

Всё внутри `sudo sh -c`: каталог закрыт для вашего пользователя, маску
`mealplan-*.db` должен раскрывать root. `mealplan.db-journal` удалять
обязательно — иначе SQLite «откатит» его в восстановленный файл. После
запуска: `curl -s 127.0.0.1:8001/api/state | head -c 200` и настройки в панели.

Проверить копию, не останавливая приложение, — поднять её во временном
контейнере на `127.0.0.1:8002`:

```sh
sudo sh -c 'rm -rf /tmp/mp-restore && mkdir /tmp/mp-restore \
  && cp "$(ls -1 /opt/meal-planner-data/backups/mealplan-*.db | tail -1)" /tmp/mp-restore/mealplan.db \
  && chown -R 10001 /tmp/mp-restore'
docker run --rm -d --name mp-restore -p 127.0.0.1:8002:8000 -v /tmp/mp-restore:/data meal-planner:local
curl -s 127.0.0.1:8002/api/recipes | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))'
docker stop mp-restore && sudo rm -rf /tmp/mp-restore
```

### Переезд на другой хост или архитектуру (Pi / aarch64)

Образ собирается на хосте из исходников, базовые образы multi-arch — на arm64
те же команды. Файл SQLite между архитектурами переносим.

1. На старом хосте: `docker exec meal-planner meal-planner-backup`, забрать
   последнюю копию из `/opt/meal-planner-data/backups`.
2. На новом — «Первая установка», шаги 1–5.
3. Копию — в `/opt/meal-planner-data/mealplan.db`,
   `sudo chown 10001 … && sudo chmod 640 …`.
4. Шаги 6–8.

Собрать arm64-образ заранее из WSL: `docker buildx build --platform
linux/arm64 -t meal-planner .` (тогда в compose — `image:` вместо `build:`).

### Неполадки

| Симптом | Причина | Что делать |
|---|---|---|
| «Не удалось загрузить план: HTTP 500», в `docker logs meal-planner` — `unable to open database file` | владелец `/opt/meal-planner-data` не 10001 (например, после `chown -R` на себя) | `sudo chown -R 10001 /opt/meal-planner-data && sudo chmod 2750 /opt/meal-planner-data{,/backups}`, `docker restart meal-planner` |
| сенсоров `meal_planner_*` нет, в логе HA ни слова о пакете | HA подняли раньше клона: Docker создал пустой каталог-источник от root, `git pull` в него не пишет | `sudo chown -R <user>: ~/meal-planner/ha && cd ~/meal-planner && git pull && git checkout -- ha`, `docker restart homeassistant` |
| сенсоры `unavailable`, в логе HA `Error fetching data` | приложение не запущено или старая версия без эндпоинта | `docker ps`, `curl -s 127.0.0.1:8001/api/summary`, пересобрать |
| панель «Питание» пустая / ошибка прокси | контейнер не поднят | `docker ps`, `curl -s 127.0.0.1:8001/api/health` |
| в мобильном приложении HA панель «Питание» без шапки, не вернуться в HA | `ui_mode` по умолчанию `normal` — голый iframe без кнопки меню | `ui_mode: toolbar` в `ingress:`, затем «Ingress: Reload» или перезапуск HA |
| Assist не знает про план | скрипты не открыты для Assist | «Голосовые ассистенты → Открыть доступ» |
| контейнер `unhealthy` | Docker его сам не перезапускает (только при падении процесса) | `docker logs meal-planner`, `docker restart meal-planner` |
| в `/opt/meal-planner-data/backups` нет свежих копий, `journalctl -t meal-planner-backup` пуст | не установлено задание cron (шаг 8) | `crontab -l`; поставить задание и прогнать его в окружении cron: `env -i PATH=/usr/bin:/bin sh -c 'docker exec meal-planner meal-planner-backup 2>&1 \| logger -t meal-planner-backup'` |
| после обновления в панели старый UI (нет новых разделов), хотя `docker exec meal-planner ls /app/frontend/dist/assets` показывает новый бандл | браузер держит `index.html` из кэша; до MP-50 он отдавался без `Cache-Control` | один раз: Ctrl+Shift+R; в приложении HA — «Настройки → Companion App → Отладка → Сбросить кэш фронтенда». С MP-50 `index.html` — `no-cache`, повторяться не должно |
| HTTP 500, в `docker logs meal-planner` — `SchemaTooNewError` | образ старее БД: откатили приложение после миграции | вернуть новый образ (`git pull`, пересборка) или восстановить копию, снятую до обновления |

## Проверка под префиксом (как HA Ingress)

`hass_ingress` отдаёт приложение под `/api/ingress/meal_planner/` (Ingress
Supervisor'а — под `/api/hassio_ingress/<token>/`) и срезает префикс. Стенд `tools/ingress-test` повторяет это на nginx под
`/ingress-test/`:

```sh
docker compose -f tools/ingress-test/compose.yaml up -d --build
python3 tools/ingress-test/check.py        # ассеты и API резолвятся от страницы
# браузер: http://localhost:8080/ingress-test/
docker compose -f tools/ingress-test/compose.yaml down -v
```

Без завершающего `/` (`/ingress-test`) прокси редиректит на `/ingress-test/`:
иначе `./assets` и `api/…` резолвились бы мимо приложения. HA сам всегда
открывает Ingress со слешем.
