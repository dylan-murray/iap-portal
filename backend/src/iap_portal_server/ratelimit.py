"""Fixed-window rate limits stored in the portal database.

Counters live in Postgres (SQLite in tests), so every portal replica enforces
the same limit without extra infrastructure. Limits are applied only to
endpoints where abuse is plausible and traffic is low: sign-in, failed bearer
authentication, app sign-in codes, and cookie-authenticated mutations. The
gateway's /auth/verify check is deliberately not limited, so normal app traffic
(assets, WebSockets, streaming) is never throttled by the portal.
"""

from __future__ import annotations

import random
import time

from fastapi import HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from iap_portal_server.config import get_settings
from iap_portal_server.db import session as db_session
from iap_portal_server.db.models import RateLimitCounter

WINDOW_SECONDS = 60
_RETENTION_SECONDS = 3600


def client_ip(request: Request) -> str:
    """Client address as seen by the outermost trusted proxy.

    With N trusted hops, the Nth X-Forwarded-For entry from the right was
    appended by our own edge proxy; entries further left are client-controlled.
    """
    hops = get_settings().trusted_proxy_hops
    if hops > 0:
        forwarded = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",")]
        forwarded = [p for p in forwarded if p]
        if len(forwarded) >= hops:
            return forwarded[-hops][:64]
    return request.client.host if request.client else "unknown"


def _window(now: float) -> int:
    return int(now) - int(now) % WINDOW_SECONDS


async def _increment(key: str) -> int:
    async with db_session.session_factory()() as db:
        dialect = db.bind.dialect.name
        insert = pg_insert if dialect == "postgresql" else sqlite_insert
        start = _window(time.time())
        stmt = (
            insert(RateLimitCounter)
            .values(key=key, window_start=start, count=1)
            .on_conflict_do_update(
                index_elements=["key", "window_start"],
                set_={"count": RateLimitCounter.count + 1},
            )
            .returning(RateLimitCounter.count)
        )
        count = (await db.execute(stmt)).scalar_one()
        if random.random() < 0.01:
            await db.execute(
                delete(RateLimitCounter).where(
                    RateLimitCounter.window_start < start - _RETENTION_SECONDS
                )
            )
        await db.commit()
        return count


async def current(key: str) -> int:
    async with db_session.session_factory()() as db:
        value = (
            await db.execute(
                select(RateLimitCounter.count).where(
                    RateLimitCounter.key == key,
                    RateLimitCounter.window_start == _window(time.time()),
                )
            )
        ).scalar_one_or_none()
        return value or 0


def _too_many() -> HTTPException:
    return HTTPException(429, "rate limit exceeded", headers={"Retry-After": str(WINDOW_SECONDS)})


async def enforce(key: str, limit: int) -> None:
    """Count one event for `key`; raise 429 once the window's limit is exceeded."""
    if limit > 0 and await _increment(key) > limit:
        raise _too_many()


async def check(key: str, limit: int) -> None:
    """Raise 429 if `key` already reached its limit, without counting."""
    if limit > 0 and await current(key) >= limit:
        raise _too_many()


async def record(key: str) -> None:
    await _increment(key)
