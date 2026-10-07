"""Access grants: user- and group-level, owner-managed."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, model_validator
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from iap_portal_server.audit import log_event
from iap_portal_server.auth.principals import Principal, get_principal
from iap_portal_server.db.models import App, AppAccess, Group, User
from iap_portal_server.db.session import get_db
from iap_portal_server.rbac.checks import user_is_app_owner

router = APIRouter(prefix="/api/apps/{slug}/access", tags=["access"])


class GrantIn(BaseModel):
    user_email: str | None = None
    group_name: str | None = None
    # Time-bound access. Supply either an absolute `expires_at` or a
    # convenience `expires_in_hours`; not both. Unset = permanent grant.
    expires_at: datetime | None = None
    expires_in_hours: float | None = None

    @model_validator(mode="after")
    def _resolve_expiry(self) -> "GrantIn":
        if self.expires_at is not None and self.expires_in_hours is not None:
            raise ValueError("specify only one of expires_at or expires_in_hours")
        if self.expires_in_hours is not None:
            if self.expires_in_hours <= 0:
                raise ValueError("expires_in_hours must be positive")
            self.expires_at = datetime.now(timezone.utc) + timedelta(hours=self.expires_in_hours)
            self.expires_in_hours = None
        return self


class GrantOut(BaseModel):
    id: int
    user_email: str | None
    group_name: str | None
    expires_at: datetime | None = None


async def _require_owner(principal: Principal, slug: str, db: AsyncSession) -> App:
    """App owners and admins (session), or the operator token."""
    app = (await db.execute(select(App).where(App.slug == slug))).scalar_one_or_none()
    if app is None:
        raise HTTPException(404)
    if principal.is_admin:
        return app
    if principal.kind != "user" or not await user_is_app_owner(db, principal.user, app):
        raise HTTPException(403, "owners only")
    return app


@router.get("", response_model=list[GrantOut])
async def list_grants(
    slug: str, principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)
):
    app = await _require_owner(principal, slug, db)
    user_rows = (
        await db.execute(
            select(AppAccess.id, User.email, AppAccess.expires_at)
            .join(User, User.id == AppAccess.user_id)
            .where(AppAccess.app_id == app.id)
        )
    ).all()
    group_rows = (
        await db.execute(
            select(AppAccess.id, Group.name, AppAccess.expires_at)
            .join(Group, Group.id == AppAccess.group_id)
            .where(AppAccess.app_id == app.id)
        )
    ).all()
    out = [
        GrantOut(id=r[0], user_email=r[1], group_name=None, expires_at=r[2])
        for r in user_rows
    ]
    out += [
        GrantOut(id=r[0], user_email=None, group_name=r[1], expires_at=r[2])
        for r in group_rows
    ]
    return out


@router.post("", response_model=GrantOut, status_code=201)
async def grant(
    slug: str,
    payload: GrantIn,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    app = await _require_owner(principal, slug, db)
    if bool(payload.user_email) == bool(payload.group_name):
        raise HTTPException(400, "specify exactly one of user_email or group_name")

    target_user_id = None
    target_group_id = None
    target_label = ""
    if payload.user_email:
        u = (
            await db.execute(
                select(User).where(User.email == payload.user_email.lower())
            )
        ).scalar_one_or_none()
        if u is None:
            raise HTTPException(404, "no such user")
        target_user_id = u.id
        target_label = u.email
    else:
        g = (
            await db.execute(select(Group).where(Group.name == payload.group_name))
        ).scalar_one_or_none()
        if g is None:
            raise HTTPException(404, "no such group")
        target_group_id = g.id
        target_label = g.name

    existing = (
        await db.execute(
            select(AppAccess).where(
                and_(
                    AppAccess.app_id == app.id,
                    AppAccess.user_id == target_user_id,
                    AppAccess.group_id == target_group_id,
                )
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        # Re-granting an existing grant refreshes its expiration — lets owners
        # "extend" a time-bound grant with a new POST, or convert a permanent
        # grant into a time-bound one (or vice versa) without a revoke cycle.
        existing.expires_at = payload.expires_at
        await db.flush()
        await log_event(
            db,
            event_type="access_granted",
            actor_email=principal.email,
            app_slug=app.slug,
            target_email=payload.user_email,
            detail={
                "principal": target_label,
                "expires_at": payload.expires_at.isoformat() if payload.expires_at else None,
                "refreshed": True,
            },
        )
        await db.commit()
        return GrantOut(
            id=existing.id,
            user_email=payload.user_email,
            group_name=payload.group_name,
            expires_at=existing.expires_at,
        )

    grant = AppAccess(
        app_id=app.id,
        user_id=target_user_id,
        group_id=target_group_id,
        granted_by_user_id=principal.user_id,
        expires_at=payload.expires_at,
    )
    db.add(grant)
    await db.flush()

    await log_event(
        db,
        event_type="access_granted",
        actor_email=principal.email,
        app_slug=app.slug,
        target_email=payload.user_email,
        detail={
            "principal": target_label,
            "expires_at": payload.expires_at.isoformat() if payload.expires_at else None,
        },
    )
    await db.commit()
    return GrantOut(
        id=grant.id,
        user_email=payload.user_email,
        group_name=payload.group_name,
        expires_at=grant.expires_at,
    )


@router.delete("/{grant_id}", status_code=204)
async def revoke(
    slug: str,
    grant_id: int,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    app = await _require_owner(principal, slug, db)
    grant = (
        await db.execute(
            select(AppAccess).where(AppAccess.id == grant_id, AppAccess.app_id == app.id)
        )
    ).scalar_one_or_none()
    if grant is None:
        raise HTTPException(404)
    await db.delete(grant)
    await log_event(
        db, event_type="access_revoked", actor_email=principal.email, app_slug=app.slug
    )
    await db.commit()
