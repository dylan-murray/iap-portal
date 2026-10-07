"""App registry CRUD — the admin API the CLI, UI, and registration Jobs use.

Callers (see auth/principals.py):
- admins (session) and the operator token: every app;
- app owners (session): view/edit/enable/disable their apps;
- registration principals (Kubernetes workload identity): create or update the
  registry entry and owner list of exactly one app, nothing else.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from iap_portal_server.audit import log_event
from iap_portal_server.auth.principals import (
    Principal,
    get_principal,
    get_user_principal,
    require_admin,
)
from iap_portal_server.auth.urls import SLUG_PATTERN, is_valid_slug
from iap_portal_server.db.models import AccessRequest, App, AppOwner, User
from iap_portal_server.db.session import get_db
from iap_portal_server.rbac.checks import user_can_access_app, user_is_app_owner

router = APIRouter(prefix="/api/apps", tags=["apps"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AppIn(BaseModel):
    slug: str = Field(pattern=SLUG_PATTERN)
    display_name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    icon_url: str | None = Field(default=None, max_length=1024)
    upstream_service: str = Field(max_length=512)  # e.g. annotation.iap-app-annotation.svc.cluster.local
    upstream_port: int = Field(default=8080, ge=1, le=65535)
    health_check_path: str = Field(default="/healthz", max_length=255)
    owners: list[str] = []  # emails

    @field_validator("icon_url")
    @classmethod
    def _icon_scheme(cls, value: str | None) -> str | None:
        if value and not value.startswith(("https://", "http://", "/")):
            raise ValueError("icon_url must be an http(s) URL or a path")
        return value or None

    @field_validator("owners")
    @classmethod
    def _owner_emails(cls, value: list[str]) -> list[str]:
        cleaned = [e.strip().lower() for e in value if e.strip()]
        for email in cleaned:
            if len(email) > 320 or not _EMAIL_RE.match(email):
                raise ValueError(f"invalid owner email: {email}")
        return cleaned


class AppOut(BaseModel):
    id: int
    slug: str
    display_name: str
    description: str | None
    icon_url: str | None
    upstream_service: str
    upstream_port: int
    health_check_path: str
    is_enabled: bool
    owners: list[str]


async def _to_out(db: AsyncSession, app: App) -> AppOut:
    rows = (
        await db.execute(
            select(User.email).join(AppOwner, AppOwner.user_id == User.id).where(
                AppOwner.app_id == app.id
            )
        )
    ).all()
    return AppOut(
        id=app.id,
        slug=app.slug,
        display_name=app.display_name,
        description=app.description,
        icon_url=app.icon_url,
        upstream_service=app.upstream_service,
        upstream_port=app.upstream_port,
        health_check_path=app.health_check_path,
        is_enabled=app.is_enabled,
        owners=[r[0] for r in rows],
    )


async def _get_app(db: AsyncSession, slug: str) -> App:
    app = (await db.execute(select(App).where(App.slug == slug))).scalar_one_or_none()
    if app is None:
        raise HTTPException(404)
    return app


async def _authorize(
    principal: Principal,
    db: AsyncSession,
    slug: str,
    app: App | None,
    *,
    owners: bool,
    registration: bool,
) -> None:
    """Admins always; owners and the app's registration identity when allowed."""
    if principal.is_admin:
        return
    if principal.kind == "registration" and registration and principal.app_slug == slug:
        return
    if (
        principal.kind == "user"
        and owners
        and app is not None
        and await user_is_app_owner(db, principal.user, app)
    ):
        return
    raise HTTPException(403, "not allowed to manage this app")


async def _user_for_email(db: AsyncSession, email: str) -> User:
    """Existing user, or a placeholder claimed by that email's first verified sign-in."""
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None:
        user = User(email=email, is_admin=False)
        db.add(user)
        await db.flush()
    return user


@router.get("", response_model=list[AppOut])
async def list_apps(
    principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)
):
    require_admin(principal)
    apps = (await db.execute(select(App).order_by(App.slug))).scalars().all()
    return [await _to_out(db, a) for a in apps]


class BrowsableApp(BaseModel):
    slug: str
    display_name: str
    description: str | None
    icon_url: str | None
    pending_request_id: int | None  # if the user has already asked, carry the id


@router.get("/browsable", response_model=list[BrowsableApp])
async def browsable_apps(
    principal: Principal = Depends(get_user_principal), db: AsyncSession = Depends(get_db)
):
    """Enabled apps the current user does NOT yet have access to. Powers the
    'request access' discovery flow on the dashboard."""
    user = principal.user
    apps = (
        await db.execute(
            select(App).where(App.is_enabled.is_(True)).order_by(App.display_name)
        )
    ).scalars().all()

    pending_by_app = dict(
        (
            await db.execute(
                select(AccessRequest.app_id, AccessRequest.id).where(
                    AccessRequest.requester_user_id == user.id,
                    AccessRequest.status == "pending",
                )
            )
        ).all()
    )

    out: list[BrowsableApp] = []
    for a in apps:
        if await user_can_access_app(db, user, a):
            continue
        out.append(
            BrowsableApp(
                slug=a.slug,
                display_name=a.display_name,
                description=a.description,
                icon_url=a.icon_url,
                pending_request_id=pending_by_app.get(a.id),
            )
        )
    return out


@router.get("/{slug}", response_model=AppOut)
async def get_app(
    slug: str, principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)
):
    # Owners and admins can view an app's detail (needed for the manage page).
    app = await _get_app(db, slug)
    await _authorize(principal, db, slug, app, owners=True, registration=True)
    return await _to_out(db, app)


@router.post("", response_model=AppOut, status_code=201)
async def create_app(
    payload: AppIn,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    await _authorize(principal, db, payload.slug, None, owners=False, registration=True)
    if not is_valid_slug(payload.slug):
        raise HTTPException(422, f"slug {payload.slug} is reserved")

    existing = (
        await db.execute(select(App).where(App.slug == payload.slug))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(409, f"app {payload.slug} already exists")

    app = App(
        slug=payload.slug,
        display_name=payload.display_name,
        description=payload.description,
        icon_url=payload.icon_url,
        upstream_service=payload.upstream_service,
        upstream_port=payload.upstream_port,
        health_check_path=payload.health_check_path,
        is_enabled=True,
    )
    db.add(app)
    await db.flush()

    # Seed owner rows. Include the acting user iff they're a real human.
    owner_emails = set(payload.owners)
    if principal.user is not None:
        owner_emails.add(principal.user.email.lower())
    for email in sorted(owner_emails):
        owner = await _user_for_email(db, email)
        db.add(AppOwner(app_id=app.id, user_id=owner.id))

    await log_event(
        db,
        event_type="app_created",
        actor_email=principal.email,
        app_slug=app.slug,
        detail=payload.model_dump(),
    )
    await db.commit()
    await db.refresh(app)
    return await _to_out(db, app)


class OwnerIn(BaseModel):
    email: str


@router.post("/{slug}/owners", response_model=AppOut, status_code=201)
async def add_owner(
    slug: str,
    payload: OwnerIn,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    require_admin(principal)
    app = await _get_app(db, slug)
    target = (
        await db.execute(select(User).where(User.email == payload.email.lower()))
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(404, f"no user with email {payload.email}")

    existing = (
        await db.execute(
            select(AppOwner).where(
                AppOwner.app_id == app.id, AppOwner.user_id == target.id
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(AppOwner(app_id=app.id, user_id=target.id))
        await db.flush()
        await log_event(
            db,
            event_type="owner_added",
            actor_email=principal.email,
            app_slug=app.slug,
            target_email=target.email,
        )
    await db.commit()
    return await _to_out(db, app)


@router.delete("/{slug}/owners/{email}", status_code=204)
async def remove_owner(
    slug: str,
    email: str,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    require_admin(principal)
    app = await _get_app(db, slug)
    target = (
        await db.execute(select(User).where(User.email == email.lower()))
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(404, f"no user with email {email}")

    owner = (
        await db.execute(
            select(AppOwner).where(
                AppOwner.app_id == app.id, AppOwner.user_id == target.id
            )
        )
    ).scalar_one_or_none()
    if owner is None:
        raise HTTPException(404, "not an owner")

    # Guardrail: don't strand the app with zero owners.
    owner_count = len(
        (await db.execute(select(AppOwner).where(AppOwner.app_id == app.id))).all()
    )
    if owner_count <= 1:
        raise HTTPException(400, "cannot remove the last owner")

    await db.delete(owner)
    await log_event(
        db,
        event_type="owner_removed",
        actor_email=principal.email,
        app_slug=app.slug,
        target_email=target.email,
    )
    await db.commit()


@router.put("/{slug}", response_model=AppOut)
async def update_app(
    slug: str,
    payload: AppIn,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    if payload.slug != slug:
        raise HTTPException(422, "slug in body must match the URL")
    app = await _get_app(db, slug)
    await _authorize(principal, db, slug, app, owners=True, registration=True)

    app.display_name = payload.display_name
    app.description = payload.description
    app.icon_url = payload.icon_url
    app.upstream_service = payload.upstream_service
    app.upstream_port = payload.upstream_port
    app.health_check_path = payload.health_check_path

    # Owner reconciliation — service callers only (registration Job, operator CLI).
    # This makes `iap-app.yaml → registration.owners` the source of truth on every
    # deploy, while human owner edits via the UI endpoints are preserved (they
    # don't hit this branch). Skipped when payload.owners is empty, to avoid a
    # broken yaml wiping ownership.
    if principal.kind in {"registration", "operator"} and payload.owners:
        desired = set(payload.owners)

        current_rows = (
            await db.execute(
                select(AppOwner, User.email)
                .join(User, User.id == AppOwner.user_id)
                .where(AppOwner.app_id == app.id)
            )
        ).all()
        current_by_email = {email: ao for (ao, email) in current_rows}

        for email, ao in current_by_email.items():
            if email not in desired:
                await db.delete(ao)
                await log_event(
                    db,
                    event_type="owner_removed",
                    actor_email=principal.email,
                    app_slug=app.slug,
                    target_email=email,
                    detail={"source": "yaml-reconcile"},
                )

        for email in sorted(desired - set(current_by_email)):
            owner = await _user_for_email(db, email)
            db.add(AppOwner(app_id=app.id, user_id=owner.id))
            await log_event(
                db,
                event_type="owner_added",
                actor_email=principal.email,
                app_slug=app.slug,
                target_email=email,
                detail={"source": "yaml-reconcile"},
            )

    await log_event(
        db,
        event_type="app_updated",
        actor_email=principal.email,
        app_slug=app.slug,
        detail=payload.model_dump(),
    )
    await db.commit()
    return await _to_out(db, app)


async def _set_enabled(principal: Principal, db: AsyncSession, slug: str, enabled: bool) -> AppOut:
    app = await _get_app(db, slug)
    # Registration identities cannot re-enable an app an owner or admin disabled.
    await _authorize(principal, db, slug, app, owners=True, registration=False)
    if app.is_enabled != enabled:
        app.is_enabled = enabled
        await log_event(
            db,
            event_type="app_enabled" if enabled else "app_disabled",
            actor_email=principal.email,
            app_slug=app.slug,
        )
        await db.commit()
    return await _to_out(db, app)


@router.post("/{slug}/disable", response_model=AppOut)
async def disable_app(
    slug: str, principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)
):
    """Hide the app from dashboards and block ext_authz. Idempotent."""
    return await _set_enabled(principal, db, slug, False)


@router.post("/{slug}/enable", response_model=AppOut)
async def enable_app(
    slug: str, principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)
):
    """Re-enable a previously disabled app. Idempotent."""
    return await _set_enabled(principal, db, slug, True)
