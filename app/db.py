from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.domain import TARIFFS
from app.models import Tariff


def create_engine(url: str, *, use_pool: bool = True) -> AsyncEngine:
    """Создаёт движок. Вызывается из lifespan приложения.

    Движок намеренно не создаётся на уровне модуля: он привязывается к циклу
    событий, в котором использовался, и в тестах это ломается.
    use_pool=False (NullPool) - для тестов: соединения не переживают цикл событий.
    """
    if use_pool:
        return create_async_engine(url)
    return create_async_engine(url, poolclass=NullPool)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: после commit атрибуты объекта остаются доступны.
    # Иначе их чтение вызовет скрытый запрос и ошибку MissingGreenlet.
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Зависимость FastAPI: одна сессия на запрос."""
    async with request.app.state.session_factory() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def seed_tariffs(session: AsyncSession) -> None:
    """Создаёт тарифы при старте. Повторный запуск ничего не дублирует и не меняет."""
    rows = [{"id": id_, "title": title, "price": price} for id_, title, price in TARIFFS]
    stmt = insert(Tariff).values(rows).on_conflict_do_nothing(index_elements=["id"])
    await session.execute(stmt)
    await session.commit()
