import asyncio
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url

from app.config import get_database_url
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent


def _test_database_url() -> URL:
    """Адрес тестовой базы: имя рабочей базы + `_test`."""
    url = make_url(get_database_url())
    if not url.database.endswith("_test"):
        url = url.set(database=f"{url.database}_test")
    return url


async def _create_database_if_missing(url: URL) -> None:
    # Подключаемся к служебной базе postgres: к несуществующей базе подключиться нельзя.
    admin_dsn = url.set(drivername="postgresql", database="postgres")
    conn = await asyncpg.connect(admin_dsn.render_as_string(hide_password=False))
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", url.database)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{url.database}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def database_url():
    """Готовит тестовую базу. Фикстура синхронная намеренно.

    Alembic внутри вызывает asyncio.run(), а он падает, если цикл событий уже запущен.
    Синхронная сессионная фикстура выполняется вне цикла, поэтому проблемы нет.
    """
    url = _test_database_url()
    # Страховка: дальше мы делаем TRUNCATE, на рабочей базе это недопустимо.
    assert url.database.endswith("_test"), "tests must run on a *_test database"

    asyncio.run(_create_database_if_missing(url))
    rendered = url.render_as_string(hide_password=False)

    # Alembic берёт адрес из DATABASE_URL; на время тестов указываем тестовую базу.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("DATABASE_URL", rendered)
    alembic_config = Config(str(ROOT / "alembic.ini"))
    command.downgrade(alembic_config, "base")  # чистая схема, заодно проверяем откат
    command.upgrade(alembic_config, "head")

    yield rendered
    monkeypatch.undo()


@pytest.fixture
async def app(database_url):
    """Приложение с настоящим lifespan на тестовой базе, платежи очищены."""
    application = create_app(
        database_url, use_pool=False
    )  # NullPool: соединения не живут между тестами
    # ASGITransport не запускает lifespan сам, поэтому запускаем его вручную.
    async with application.router.lifespan_context(application):
        async with application.state.session_factory() as session:
            await session.execute(text("TRUNCATE payments RESTART IDENTITY"))
            await session.commit()
        yield application


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.fixture
def session_factory(app):
    """Для проверки состояния в базе: каждый тест открывает свою сессию, не сессию приложения."""
    return app.state.session_factory
