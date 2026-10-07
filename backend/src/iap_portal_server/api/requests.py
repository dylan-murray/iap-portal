"""Access-request flow: users ask to use an app, owners/admins approve or deny."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from iap_portal_server.audit import log_event
from iap_portal_server.auth.principals import Principal, get_user_principal
from iap_portal_server.db.models import (
    AccessRequest,
    App,
    AppAccess,
    AppOwner,
    User,
)
from iap_portal_server.db.session import get_db
from iap_portal_server.rbac.checks import user_can_access_app, user_is_app_owner

apps_router = APIRouter(prefix="/api/apps", tags=["access-requests"])
router = APIRouter(prefix="/api/access-requests", tags=["access-requests"])

# Decided (approved/denied/expired) requests hide from the user's "Your
# requests" view after this many days. Pending requests stay visible forever
# so users don't lose track of things still waiting for a decision.
MINE_DECIDED_VISIBILITY_DAYS = 7


# --- Schemas ----------------------------------------------------------------


class AccessRequestCreate(BaseModel):
    reason: str | None = None


class AccessRequestDecision(BaseModel):
    note: str | None = None
    # Only meaningful on approve. None = permanent grant.
    expires_in_hours: float | None = None


class AccessRequestOut(BaseModel):
    id: int
    app_slug: str
    app_display_name: str
    requester_email: str
    reason: str | None
    status: str  # pending | approved | denied
    requested_at: datetime
    decided_at: datetime | None
    decided_by_email: str | None
    decided_note: str | None


# --- Helpers ----------------------------------------------------------------


async def _get_user(db: AsyncSession, user_id: int) -> User:
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if u is None:
        raise HTTPException(401)
    return u


async def _to_out(db: AsyncSession, ar: AccessRequest) -> AccessRequestOut:
    app = (
        await db.execute(select(App).where(App.id == ar.app_id))
    ).scalar_one()
    requester = (
        await db.execute(select(User).where(User.id == ar.requester_user_id))
    ).scalar_one()
    decider_email: str | None = None
    if ar.decided_by_user_id is not None:
        decider = (
            await db.execute(select(User).where(User.id == ar.decided_by_user_id))
        ).scalar_one_or_none()
        decider_email = decider.email if decider else None
    return AccessRequestOut(
        id=ar.id,
        app_slug=app.slug,
        app_display_name=app.display_name,
        requester_email=requester.email,
        reason=ar.reason,
        status=ar.status,
        requested_at=ar.requested_at,
        decided_at=ar.decided_at,
        decided_by_email=decider_email,
        decided_note=ar.decided_note,
    )


# --- Endpoints --------------------------------------------------------------


@apps_router.post(
    "/{slug}/access-requests", response_model=AccessRequestOut, status_code=201
)
async def create_access_request(
    slug: str,
    payload: AccessRequestCreate,
    principal: Principal = Depends(get_user_principal),
    db: AsyncSession = Depends(get_db),
):
    """Authed user requests access to `slug`. Idempotent: if the user already has
    a pending request, returns it. Rejects if the user already has access."""
    requester = principal.user
    app = (
        await db.execute(
            select(App).where(App.slug == slug, App.is_enabled.is_(True))
        )
    ).scalar_one_or_none()
    if app is None:
        raise HTTPException(404, f"no enabled app with slug {slug}")

    if await user_can_access_app(db, requester, app):
        raise HTTPException(409, "you already have access to this app")

    existing_pending = (
        await db.execute(
            select(AccessRequest).where(
                AccessRequest.app_id == app.id,
                AccessRequest.requester_user_id == requester.id,
                AccessRequest.status == "pending",
            )
        )
    ).scalar_one_or_none()
    if existing_pending is not None:
        return await _to_out(db, existing_pending)

    ar = AccessRequest(
        app_id=app.id,
        requester_user_id=requester.id,
        reason=(payload.reason or "").strip() or None,
        status="pending",
    )
    db.add(ar)
    await db.flush()

    await log_event(
        db,
        event_type="access_requested",
        actor_email=requester.email,
        app_slug=app.slug,
        detail={"reason": ar.reason} if ar.reason else None,
    )
    await db.commit()
    await db.refresh(ar)
    return await _to_out(db, ar)


@router.get("/mine", response_model=list[AccessRequestOut])
async def list_my_requests(
    principal: Principal = Depends(get_user_principal), db: AsyncSession = Depends(get_db)
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=MINE_DECIDED_VISIBILITY_DAYS)
    rows = (
        await db.execute(
            select(AccessRequest)
            .where(
                AccessRequest.requester_user_id == principal.user.id,
                or_(
                    AccessRequest.status == "pending",
                    AccessRequest.decided_at >= cutoff,
                ),
            )
            .order_by(AccessRequest.requested_at.desc())
        )
    ).scalars().all()
    return [await _to_out(db, r) for r in rows]


@router.get("/pending", response_model=list[AccessRequestOut])
async def list_pending_requests(
    principal: Principal = Depends(get_user_principal), db: AsyncSession = Depends(get_db)
):
    """Requests the caller can act on — all pending if admin, else only pending
    requests for apps the caller owns."""
    actor = principal.user

    q = select(AccessRequest).where(AccessRequest.status == "pending")
    if not actor.is_admin:
        owned_app_ids = (
            await db.execute(
                select(AppOwner.app_id).where(AppOwner.user_id == actor.id)
            )
        ).scalars().all()
        if not owned_app_ids:
            return []
        q = q.where(AccessRequest.app_id.in_(owned_app_ids))
    q = q.order_by(AccessRequest.requested_at.desc())
    rows = (await db.execute(q)).scalars().all()
    return [await _to_out(db, r) for r in rows]


async def _require_can_decide(
    db: AsyncSession, actor: User, ar: AccessRequest
) -> App:
    app = (
        await db.execute(select(App).where(App.id == ar.app_id))
    ).scalar_one_or_none()
    if app is None:
        raise HTTPException(404, "app no longer exists")
    if not await user_is_app_owner(db, actor, app):  # short-circuits on admin
        raise HTTPException(403, "only owners or admins can decide this request")
    return app


@router.post("/{request_id}/approve", response_model=AccessRequestOut)
async def approve_request(
    request_id: int,
    payload: AccessRequestDecision,
    principal: Principal = Depends(get_user_principal),
    db: AsyncSession = Depends(get_db),
):
    actor = principal.user
    ar = (
        await db.execute(select(AccessRequest).where(AccessRequest.id == request_id))
    ).scalar_one_or_none()
    if ar is None:
        raise HTTPException(404)
    if ar.status != "pending":
        raise HTTPException(409, f"request already {ar.status}")

    app = await _require_can_decide(db, actor, ar)

    # Resolve optional duration → absolute expires_at.
    expires_at = None
    if payload.expires_in_hours is not None:
        if payload.expires_in_hours <= 0:
            raise HTTPException(400, "expires_in_hours must be positive")
        expires_at = datetime.now(timezone.utc) + timedelta(hours=payload.expires_in_hours)

    # Create or refresh the grant. If one already exists, bump its expiration
    # (None = promote to permanent, timestamp = extend/shorten).
    existing_grant = (
        await db.execute(
            select(AppAccess).where(
                AppAccess.app_id == app.id,
                AppAccess.user_id == ar.requester_user_id,
                AppAccess.group_id.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing_grant is None:
        db.add(
            AppAccess(
                app_id=app.id,
                user_id=ar.requester_user_id,
                granted_by_user_id=actor.id,
                expires_at=expires_at,
            )
        )
    else:
        existing_grant.expires_at = expires_at

    ar.status = "approved"
    ar.decided_by_user_id = actor.id
    ar.decided_at = datetime.now(timezone.utc)
    ar.decided_note = (payload.note or "").strip() or None

    requester = await _get_user(db, ar.requester_user_id)
    await log_event(
        db,
        event_type="access_request_approved",
        actor_email=actor.email,
        app_slug=app.slug,
        target_email=requester.email,
        detail={
            "request_id": ar.id,
            "note": ar.decided_note,
            "expires_at": expires_at.isoformat() if expires_at else None,
        },
    )
    await db.commit()
    await db.refresh(ar)
    return await _to_out(db, ar)


@router.post("/{request_id}/deny", response_model=AccessRequestOut)
async def deny_request(
    request_id: int,
    payload: AccessRequestDecision,
    principal: Principal = Depends(get_user_principal),
    db: AsyncSession = Depends(get_db),
):
    actor = principal.user
    ar = (
        await db.execute(select(AccessRequest).where(AccessRequest.id == request_id))
    ).scalar_one_or_none()
    if ar is None:
        raise HTTPException(404)
    if ar.status != "pending":
        raise HTTPException(409, f"request already {ar.status}")

    app = await _require_can_decide(db, actor, ar)

    ar.status = "denied"
    ar.decided_by_user_id = actor.id
    ar.decided_at = datetime.now(timezone.utc)
    ar.decided_note = (payload.note or "").strip() or None

    requester = await _get_user(db, ar.requester_user_id)
    await log_event(
        db,
        event_type="access_request_denied",
        actor_email=actor.email,
        app_slug=app.slug,
        target_email=requester.email,
        detail={"request_id": ar.id, "note": ar.decided_note},
    )
    await db.commit()
    await db.refresh(ar)
    return await _to_out(db, ar)
