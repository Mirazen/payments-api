from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool


def create_engine(url: str, *, use_pool: bool = True) -> AsyncEngine:
    """Создаёт движок. Вызывается из lifespan приложения или из фикстуры тестов.

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
