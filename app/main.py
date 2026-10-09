from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_database_url, get_webhook_secret
from app.db import create_engine, create_session_factory, seed_tariffs
from app.routers import payments, tariffs, webhooks


def create_app(database_url: str, webhook_secret: str, *, use_pool: bool = True) -> FastAPI:
    """Собирает приложение. Тесты передают свою базу, свой секрет и use_pool=False."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Движок создаётся при старте, а не при импорте модуля.
        engine = create_engine(database_url, use_pool=use_pool)
        app.state.session_factory = create_session_factory(engine)
        async with app.state.session_factory() as session:
            await seed_tariffs(session)
        yield
        await engine.dispose()

    app = FastAPI(title="payments-api", lifespan=lifespan)
    app.state.webhook_secret = webhook_secret
    app.include_router(tariffs.router)
    app.include_router(payments.router)
    app.include_router(webhooks.router)
    return app


def create_app_from_env() -> FastAPI:
    """Для запуска: uvicorn app.main:create_app_from_env --factory

    Настройки читаются из переменных окружения при старте, а не при импорте модуля.
    Нет WEBHOOK_SECRET - приложение не запускается.
    """
    return create_app(get_database_url(), get_webhook_secret())
