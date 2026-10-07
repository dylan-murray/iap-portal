"""Alembic environment. Run migrations with `python -m iap_portal_server.db.migrate`."""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from iap_portal_server.db.models import Base

config = context.config
target_metadata = Base.metadata


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # SQLite needs table rebuilds for constraint changes
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_standalone() -> None:
    from iap_portal_server.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    async with engine.begin() as conn:
        await conn.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("offline SQL generation is not supported")

connection = config.attributes.get("connection")
if connection is not None:
    _run(connection)
else:  # plain `alembic` CLI during development
    asyncio.run(_run_standalone())
