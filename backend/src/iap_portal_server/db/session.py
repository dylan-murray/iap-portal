from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from iap_portal_server.config import get_settings

_settings = get_settings()
_engine = create_async_engine(_settings.database_url, pool_pre_ping=True)
_Session = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with _Session() as session:
        yield session


def engine():
    return _engine


def session_factory() -> async_sessionmaker[AsyncSession]:
    """Session factory for work that must commit independently of a request."""
    return _Session
