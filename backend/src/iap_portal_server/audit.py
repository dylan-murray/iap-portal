from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from iap_portal_server.db.models import AuditEvent


async def log_event(
    db: AsyncSession,
    *,
    event_type: str,
    actor_email: str | None = None,
    app_slug: str | None = None,
    target_email: str | None = None,
    ip: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditEvent(
            event_type=event_type,
            actor_email=actor_email,
            app_slug=app_slug,
            target_email=target_email,
            ip=ip,
            detail=detail or {},
        )
    )
    await db.commit()
