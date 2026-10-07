from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from html import escape

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from iap_portal_server.api import access, apps, dashboard, dev, dev_annotations, groups, health, login, users
from iap_portal_server.api import requests as access_requests
from iap_portal_server.auth import verify as ext_authz
from iap_portal_server.auth.jwks import current_jwks
from iap_portal_server.auth.session import oauth_cookie_name
from iap_portal_server.config import get_settings
from iap_portal_server.db.migrate import assert_current, migrate
from iap_portal_server.middleware import (
    OriginCheckMiddleware,
    SecurityHeadersMiddleware,
    StripInboundIdentityMiddleware,
)
from iap_portal_server.security import enforce_settings

_API_PREFIXES = ("api/", "auth/", "login", "logout", "dev/", ".well-known/", "healthz")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if get_settings().auto_migrate:
        await migrate()
    else:
        await assert_current()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    enforce_settings(settings)
    # Interactive API docs only outside production.
    docs = settings.env != "production"
    app = FastAPI(
        title="iap-portal",
        lifespan=lifespan,
        redirect_slashes=False,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )

    # Middleware ordering: the last one added runs first (outermost).
    # SessionMiddleware only holds OIDC state/nonce/PKCE during sign-in; the
    # portal session itself is a server-side session (auth/session.py).
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie=oauth_cookie_name(settings),
        max_age=600,
        same_site="lax",
        https_only=settings.secure_cookies,
    )
    app.add_middleware(OriginCheckMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(StripInboundIdentityMiddleware)

    app.include_router(health.router)
    app.include_router(login.router)
    app.include_router(apps.router)
    app.include_router(access.router)
    app.include_router(groups.router)
    app.include_router(users.router)
    app.include_router(dashboard.router)
    app.include_router(access_requests.apps_router)
    app.include_router(access_requests.router)

    if settings.env == "dev" and settings.enable_dev_annotations:
        app.include_router(dev_annotations.router)

    if settings.dev_login_enabled:
        app.include_router(dev.router)

    @app.get("/api/config")
    async def public_config():
        return JSONResponse({"display_name": get_settings().display_name}, headers={"Cache-Control": "no-store"})

    @app.get("/.well-known/jwks.json")
    async def jwks():
        return JSONResponse(current_jwks(), headers={"Cache-Control": "public, max-age=300"})

    static_dir = Path(os.environ.get("PORTAL_STATIC_DIR", "/app/static"))
    index_html = static_dir / "index.html"

    if index_html.exists():
        assets_dir = static_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            if full_path.startswith(_API_PREFIXES):
                raise HTTPException(status_code=404)
            html = index_html.read_text().replace(
                "<title>iap-portal</title>",
                f"<title>{escape(get_settings().display_name)}</title>",
            )
            return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    elif settings.env == "dev":
        # Native dev: no SPA bundle in the backend; vite serves it on :5173.
        @app.get("/", include_in_schema=False)
        async def _dev_root_bounce():
            return RedirectResponse(settings.portal_origin + "/")

    return app


def create_authorization_app() -> FastAPI:
    """Private gateway listener; never mount this router on the public app.

    In Kubernetes, the pod's L4 policy admits only the gateway's mTLS identity
    on port 8091. Compose places this listener on gateway/database-only networks.
    """
    enforce_settings(get_settings())
    app = FastAPI(
        title="iap-portal-authorization",
        lifespan=lifespan,
        redirect_slashes=False,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(StripInboundIdentityMiddleware)
    app.include_router(health.router)
    app.include_router(ext_authz.router)
    return app


app = create_app()
