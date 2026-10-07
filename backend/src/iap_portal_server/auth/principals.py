"""Who is calling the portal API, and what they may do.

- user:         a browser session (portal cookie). Admin status comes from
                PORTAL_ADMIN_EMAILS and is re-evaluated on every request.
- operator:     PORTAL_ADMIN_API_TOKEN. Admin-equivalent; for operators and the
                CLI, never distributed to app namespaces.
- registration: a Kubernetes ServiceAccount token that may only create or update
                the registry entry of one app (see auth/k8s.py).

A request carrying `Authorization: Bearer` is authenticated by that token alone.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from iap_portal_server import ratelimit
from iap_portal_server.auth.k8s import (
    TokenReviewUnavailable,
    looks_like_jwt,
    registration_slug,
    review_token,
)
from iap_portal_server.auth.session import load_portal_session
from iap_portal_server.config import get_settings
from iap_portal_server.db.models import AuthSession, User
from iap_portal_server.db.session import get_db

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass
class Principal:
    kind: str  # "user" | "operator" | "registration"
    user: User | None = None
    session: AuthSession | None = None
    app_slug: str | None = None

    @property
    def email(self) -> str:
        if self.user is not None:
            return self.user.email
        return "operator" if self.kind == "operator" else f"registration:{self.app_slug}"

    @property
    def is_admin(self) -> bool:
        return self.kind == "operator" or (self.user is not None and self.user.is_admin)

    @property
    def user_id(self) -> int | None:
        return self.user.id if self.user is not None else None


def sync_admin(user: User) -> None:
    """Mirror configured admin status onto the user row for this request."""
    settings = get_settings()
    configured = user.email.lower() in settings.admin_email_set
    if settings.dev_login_enabled and user.is_admin:
        return  # dev login may grant admin locally
    if user.is_admin != configured:
        user.is_admin = configured


def bearer_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


async def _bearer_principal(request: Request, token: str) -> Principal:
    settings = get_settings()
    key = f"bearer-fail:{ratelimit.client_ip(request)}"
    await ratelimit.check(key, settings.rate_limit_bearer_failures_per_minute)

    configured = settings.admin_api_token
    if configured and hmac.compare_digest(token.encode(), configured.encode()):
        return Principal(kind="operator")

    if settings.kubernetes_registration_enabled and looks_like_jwt(token):
        try:
            username = await review_token(token, settings)
        except TokenReviewUnavailable:
            raise HTTPException(503, "token review unavailable")
        slug = registration_slug(username, settings)
        if slug is not None:
            return Principal(kind="registration", app_slug=slug)

    await ratelimit.record(key)
    raise HTTPException(401, "invalid bearer token")


async def optional_user(request: Request, db: AsyncSession) -> tuple[AuthSession, User] | None:
    loaded = await load_portal_session(request, db)
    if loaded is not None:
        sync_admin(loaded[1])
    return loaded


async def _session_principal(request: Request, db: AsyncSession) -> Principal:
    loaded = await optional_user(request, db)
    if loaded is None:
        raise HTTPException(401)
    session, user = loaded
    if request.method in UNSAFE_METHODS:
        await ratelimit.enforce(
            f"mutation:{user.id}", get_settings().rate_limit_mutations_per_minute
        )
    return Principal(kind="user", user=user, session=session)


async def get_principal(request: Request, db: AsyncSession = Depends(get_db)) -> Principal:
    token = bearer_token(request)
    if token is not None:
        return await _bearer_principal(request, token)
    return await _session_principal(request, db)


async def get_user_principal(request: Request, db: AsyncSession = Depends(get_db)) -> Principal:
    """Browser-session callers only (end-user features such as access requests)."""
    if bearer_token(request) is not None:
        raise HTTPException(403, "this endpoint requires a signed-in user")
    return await _session_principal(request, db)


def require_admin(principal: Principal) -> Principal:
    if not principal.is_admin:
        raise HTTPException(403, "admin only")
    return principal
