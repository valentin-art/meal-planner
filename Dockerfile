# syntax=docker/dockerfile:1
# Образ приложения: собранный frontend/ + FastAPI + SQLite.
#
#   docker build -t meal-planner .
#   docker run -p 8000:8000 -v meal-planner-data:/data -e TZ=Europe/Berlin meal-planner

# Статика от платформы не зависит: собираем на платформе сборки, а не под
# QEMU для arm64.
FROM --platform=$BUILDPLATFORM node:24-slim AS frontend

WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci

COPY frontend/ ./
RUN npm run build


FROM python:3.12-slim-trixie AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.12 /uv /bin/

# Системный python образа, байткод заранее, без хардлинков из кэша.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Зависимости — отдельным слоем: пересобирается только при смене uv.lock.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-install-project

# README.md нужен uv_build (readme = в pyproject.toml).
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
# --no-editable: пакет ставится в site-packages, src/ в рантайме не нужен.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable


FROM python:3.12-slim-trixie

RUN useradd --system --create-home --uid 10001 app \
    && mkdir /data \
    && chown app:app /data

# Тот же путь и тот же базовый образ, что в builder: шебанги и симлинк
# на python остаются валидными.
COPY --from=builder /app/.venv /app/.venv
COPY --from=frontend /frontend/dist /app/frontend/dist

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    MEALPLAN_DB_PATH=/data/mealplan.db \
    MEALPLAN_STATIC_DIR=/app/frontend/dist \
    MEALPLAN_PORT=8000

USER app
WORKDIR /app
VOLUME /data
EXPOSE 8000

# Exec-форма: entry point пакета — PID 1 и получает SIGTERM от docker stop;
# порт он берёт из MEALPLAN_PORT сам.
CMD ["meal-planner"]
