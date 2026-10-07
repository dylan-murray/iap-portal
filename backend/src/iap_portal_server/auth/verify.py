"""Private HTTP ext_authz endpoint for the gateway (Istio CUSTOM or Envoy).

Mounted only by create_authorization_app on the private listener, never by the
public portal application. Deployment policies restrict callers to the gateway.

The gateway calls `/auth/verify<original path>` for every request to an app host,
with the original request's Host and method. Only these inputs are trusted:
the check request's Host (the authority the gateway routes on), its path, and
the app session cookie. X-Forwarded-Host and similar client headers are ignored.

Responses:
  200 + identity headers + JWT  → gateway forwards the request to the app
  302 → portal /auth/app-login  → no app session (sign-in, deep link preserved)
  302 + Set-Cookie              → /.iap-portal/callback completed app sign-in
  400/403/404                   → denied (malformed host, portal or non-app host,
                                  cross-origin request, no access, unknown app)
The portal host itself is excluded from the gateway policy and always denied here.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from urllib.parse import parse_qs, quote

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from iap_portal_server.audit import log_event
from iap_portal_server.auth.jwt import mint_for_app
from iap_portal_server.auth.principals import sync_admin
from iap_portal_server.auth.session import (
    app_cookie_name,
    as_utc,
    create_app_session,
    load_app_session,
    load_portal_session_by_id,
    set_app_cookie,
    strip_portal_cookies,
    token_hash,
    utcnow,
)
from iap_portal_server.auth.urls import app_origin, app_slug_for_host, normalize_host, portal_url
from iap_portal_server.config import get_settings
from iap_portal_server.db.models import App, AppLoginCode
from iap_portal_server.db.session import get_db
from iap_portal_server.ratelimit import client_ip
from iap_portal_server.rbac.checks import user_can_access_app, user_groups

router = APIRouter()

PREFIX = "/auth/verify"
CALLBACK_PATH = "/.iap-portal/callback"
IDENTITY_HEADERS = (
    "x-forwarded-email",
    "x-forwarded-user",
    "x-forwarded-name",
    "x-forwarded-groups",
    "x-iap-portal-app",
)
_NO_STORE = {"Cache-Control": "no-store"}

# Envoy ext_authz preserves the original request method when calling the auth
# service, so this handler must accept every method the apps do.
_VERIFY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]

# One access_denied audit row per (user, app) per window and replica, so repeated
# denied requests cannot grow the audit table without bound.
_DENIAL_WINDOW_SECONDS = 60
_DENIAL_CACHE_SIZE = 10_000
_recent_denials: OrderedDict[tuple[int, str], float] = OrderedDict()


def _deny(status: int) -> Response:
    return Response(status_code=status, headers=_NO_STORE)


def _redirect(location: str, **headers: str) -> Response:
    return Response(status_code=302, headers={"Location": location, **_NO_STORE, **headers})


def _original_path(request: Request) -> str:
    """Original path and query from the check request (`/auth/verify<path>?<query>`)."""
    raw = request.scope.get("raw_path") or request.url.path.encode()
    path = raw.decode("latin-1")[len(PREFIX):] or "/"
    if not path.startswith("/"):
        path = "/" + path
    query = request.scope.get("query_string", b"").decode("latin-1")
    return f"{path}?{query}" if query else path


def _app_login_redirect(slug: str, path: str) -> Response:
    rd = app_origin(slug) + path
    return _redirect(portal_url(f"/auth/app-login?rd={quote(rd, safe='')}"))


def _should_audit_denial(user_id: int, slug: str) -> bool:
    now = time.monotonic()
    key = (user_id, slug)
    last = _recent_denials.get(key)
    if last is not None and now - last < _DENIAL_WINDOW_SECONDS:
        return False
    _recent_denials[key] = now
    _recent_denials.move_to_end(key)
    while len(_recent_denials) > _DENIAL_CACHE_SIZE:
        _recent_denials.popitem(last=False)
    return True


async def _complete_app_login(
    request: Request, db: AsyncSession, slug: str, path: str
) -> Response:
    """Redeem a single-use code and set the host-only app session cookie."""
    restart = _app_login_redirect(slug, "/")
    code = (parse_qs(path.partition("?")[2]).get("code") or [""])[0]
    if not code:
        return restart
    now = utcnow()
    row = (
        await db.execute(
            select(AppLoginCode).where(
                AppLoginCode.code_hash == token_hash(code), AppLoginCode.app_slug == slug
            )
        )
    ).scalar_one_or_none()
    if row is None or row.used_at is not None or as_utc(row.expires_at) <= now:
        return restart
    claimed = await db.execute(
        update(AppLoginCode)
        .where(AppLoginCode.id == row.id, AppLoginCode.used_at.is_(None))
        .values(used_at=now)
    )
    if claimed.rowcount != 1:
        return restart
    parent = await load_portal_session_by_id(db, row.session_id)
    if parent is None:
        await db.commit()
        return restart
    token, session = await create_app_session(db, parent[0], slug)
    await db.commit()
    response = _redirect(app_origin(slug) + row.return_path)
    set_app_cookie(response, token, session.expires_at)
    return response


@router.api_route(PREFIX, methods=_VERIFY_METHODS, include_in_schema=False)
@router.api_route(PREFIX + "/{rest:path}", methods=_VERIFY_METHODS, include_in_schema=False)
async def verify(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    settings = get_settings()
    host = normalize_host(request.headers.get("host"))
    if host is None:
        return _deny(400)
    slug = app_slug_for_host(host, settings)
    if slug is None:
        # Portal host, bare apps domain, reserved names, or foreign hosts.
        return _deny(403)

    origin = request.headers.get("origin")
    if settings.enforce_app_same_origin and origin is not None and origin != app_origin(slug):
        return _deny(403)

    path = _original_path(request)
    if path.partition("?")[0] == CALLBACK_PATH:
        return await _complete_app_login(request, db, slug, path)

    token = request.cookies.get(app_cookie_name(settings))
    loaded = await load_app_session(db, token, slug) if token else None
    if loaded is None:
        return _app_login_redirect(slug, path)
    _, user = loaded
    sync_admin(user)

    app = (
        await db.execute(select(App).where(App.slug == slug, App.is_enabled.is_(True)))
    ).scalar_one_or_none()
    if app is None:
        return _deny(404)

    if not await user_can_access_app(db, user, app):
        if _should_audit_denial(user.id, slug):
            await log_event(
                db,
                event_type="access_denied",
                actor_email=user.email,
                app_slug=slug,
                ip=client_ip(request),
                detail={"path": path.partition("?")[0][:512], "method": request.method},
            )
        return _deny(403)

    groups = await user_groups(db, user)
    jwt_token = mint_for_app(
        user_id=user.id, email=user.email, name=user.name, groups=groups, app_slug=slug
    )
    values = {
        "x-forwarded-email": user.email,
        "x-forwarded-user": str(user.id),
        "x-forwarded-name": user.name or "",
        "x-forwarded-groups": ",".join(groups),
        "x-iap-portal-app": slug,
    }
    headers = {name: value for name, value in values.items() if value}
    headers["authorization"] = f"Bearer {jwt_token}"
    # Client-supplied identity headers never reach the app: non-empty values are
    # overwritten above, empty ones and portal cookies are removed.
    remove = [name for name, value in values.items() if not value]
    cookie = request.headers.get("cookie")
    if cookie is not None:
        filtered = strip_portal_cookies(cookie)
        if not filtered:
            remove.append("cookie")
        elif filtered != cookie:
            headers["cookie"] = filtered
    if remove:
        headers["x-envoy-auth-headers-to-remove"] = ",".join(remove)
    return Response(status_code=200, headers=headers)
