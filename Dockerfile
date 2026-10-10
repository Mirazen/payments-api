FROM python:3.12-slim

# uv берём из официального образа; версия совпадает с той, что использовалась при разработке.
COPY --from=ghcr.io/astral-sh/uv:0.10.4 /uv /bin/uv

WORKDIR /app

# Сначала зависимости: этот слой пересобирается только при изменении pyproject.toml или uv.lock.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Миграции применяются при каждом старте (повторный запуск безопасен), затем стартует сервер.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:create_app_from_env --factory --host 0.0.0.0 --port 8000"]
