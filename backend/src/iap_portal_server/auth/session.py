"""Server-side portal and app sessions.

Cookies carry a random 256-bit token; the database stores its SHA-256 hash with
an absolute expiry. All session cookies are host-only. Over HTTPS they use the
`__Host-` prefix, so a sibling subdomain cannot set, overwrite, or shadow them.

- Portal session: cookie on the portal host only. Never sent to apps.
- App session: cookie on one app host, bound to that app's slug and to the
  portal session that created it. Revoking the portal session revokes it.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Request, Response
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from iap_portal_server.config import Settings, get_settings
from iap_portal_server.db.models import AuthSession, User

KIND_PORTAL = "portal"
KIND_APP = "app"

_PORTAL_COOKIE = "iap_portal_session"
_APP_COOKIE = "iap_app_session"
_OAUTH_COOKIE = "iap_portal_oauth"


def _cookie_name(base: str, settings: Settings) -> str:
    return f"__Host-{base}" if settings.secure_cookies else base


def portal_cookie_name(settings: Settings | None = None) -> str:
    return _cookie_name(_PORTAL_COOKIE, settings or get_settings())


def app_cookie_name(settings: Settings | None = None) -> str:
    return _cookie_name(_APP_COOKIE, settings or get_settings())


def oauth_cookie_name(settings: Settings | None = None) -> str:
    return _cookie_name(_OAUTH_COOKIE, settings or get_settings())


# Every cookie name the portal has ever issued, including the earlier
# parent-domain session cookie. Removed from requests forwarded to apps.
PORTAL_MANAGED_COOKIES = frozenset(
    name
    for base in (_PORTAL_COOKIE, _APP_COOKIE, _OAUTH_COOKIE)
    for name in (base, f"__Host-{base}")
)

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes; treat them as UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _set_cookie(response: Response, name: str, token: str, expires_at: datetime) -> None:
    settings = get_settings()
    max_age = max(0, int((as_utc(expires_at) - utcnow()).total_seconds()))
    response.set_cookie(
        key=name,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )


def set_portal_cookie(response: Response, token: str, expires_at: datetime) -> None:
    _set_cookie(response, portal_cookie_name(), token, expires_at)


def set_app_cookie(response: Response, token: str, expires_at: datetime) -> None:
    _set_cookie(response, app_cookie_name(), token, expires_at)


def clear_portal_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        key=portal_cookie_name(settings), path="/", secure=settings.secure_cookies, httponly=True
    )


async def create_portal_session(
    db: AsyncSession, user: User, provider: str
) -> tuple[str, AuthSession]:
    token = new_token()
    row = AuthSession(
        token_hash=token_hash(token),
        kind=KIND_PORTAL,
        user_id=user.id,
        provider=provider,
        expires_at=utcnow() + timedelta(seconds=get_settings().session_ttl_seconds),
    )
    db.add(row)
    await db.flush()
    return token, row


async def create_app_session(
    db: AsyncSession, parent: AuthSession, app_slug: str
) -> tuple[str, AuthSession]:
    token = new_token()
    expires_at = min(
        as_utc(parent.expires_at),
        utcnow() + timedelta(seconds=get_settings().app_session_ttl_seconds),
    )
    row = AuthSession(
        token_hash=token_hash(token),
        kind=KIND_APP,
        user_id=parent.user_id,
        app_slug=app_slug,
        parent_id=parent.id,
        provider=parent.provider,
        expires_at=expires_at,
    )
    db.add(row)
    await db.flush()
    return token, row


def _active(model, now: datetime):
    return (model.revoked_at.is_(None), model.expires_at > now)


async def load_portal_session_by_id(
    db: AsyncSession, session_id: int
) -> tuple[AuthSession, User] | None:
    now = utcnow()
    row = (
        await db.execute(
            select(AuthSession, User)
            .join(User, User.id == AuthSession.user_id)
            .where(
                AuthSession.id == session_id,
                AuthSession.kind == KIND_PORTAL,
                *_active(AuthSession, now),
                User.disabled_at.is_(None),
            )
        )
    ).first()
    return (row[0], row[1]) if row else None


async def load_portal_session(
    request: Request, db: AsyncSession
) -> tuple[AuthSession, User] | None:
    token = request.cookies.get(portal_cookie_name())
    if not token:
        return None
    now = utcnow()
    row = (
        await db.execute(
            select(AuthSession, User)
            .join(User, User.id == AuthSession.user_id)
            .where(
                AuthSession.token_hash == token_hash(token),
                AuthSession.kind == KIND_PORTAL,
                *_active(AuthSession, now),
                User.disabled_at.is_(None),
            )
        )
    ).first()
    return (row[0], row[1]) if row else None


async def load_app_session(
    db: AsyncSession, token: str, app_slug: str
) -> tuple[AuthSession, User] | None:
    """Valid app session for exactly this app, with a live parent portal session."""
    now = utcnow()
    parent = aliased(AuthSession)
    row = (
        await db.execute(
            select(AuthSession, User)
            .join(parent, parent.id == AuthSession.parent_id)
            .join(User, User.id == AuthSession.user_id)
            .where(
                AuthSession.token_hash == token_hash(token),
                AuthSession.kind == KIND_APP,
                AuthSession.app_slug == app_slug,
                *_active(AuthSession, now),
                *_active(parent, now),
                User.disabled_at.is_(None),
            )
        )
    ).first()
    return (row[0], row[1]) if row else None


async def revoke_session_tree(db: AsyncSession, session_id: int) -> None:
    """Revoke a portal session and every app session created from it."""
    await db.execute(
        update(AuthSession)
        .where(
            or_(AuthSession.id == session_id, AuthSession.parent_id == session_id),
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=utcnow())
    )


async def revoke_user_sessions(db: AsyncSession, user_id: int) -> None:
    await db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


def strip_portal_cookies(cookie_header: str) -> str:
    """Remove portal-managed cookies from a Cookie header, keeping the rest verbatim."""
    kept = []
    for part in cookie_header.split(";"):
        name = part.split("=", 1)[0].strip()
        if name and name not in PORTAL_MANAGED_COOKIES:
            kept.append(part.strip())
    return "; ".join(kept)
