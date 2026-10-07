"""FastAPI helpers.

    Quick start (one line):

        from fastapi import FastAPI
        from iap_portal.auth import current_user
        from iap_portal.auth.fastapi import protect

        app = FastAPI()
        protect(app, public_paths=["/healthz"])

        @app.get("/")
        def home():
            return {"hi": current_user().email}

    Power-user primitives (if `protect` is too opinionated):

        - IapPortalAuthMiddleware(enforce=True, public_paths=[...])
        - require_user  (FastAPI dependency)
        - install_unauthorized_handler(app)
"""

from __future__ import annotations

from html import escape
from typing import Iterable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from iap_portal.auth.core import (
    AuthError,
    User,
    reset_current,
    set_current,
    user_from_request,
)


def require_user(request: Request) -> User:
    try:
        return user_from_request(request.headers)
    except AuthError as e:
        raise HTTPException(401, str(e))


def _wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    # Browsers send Accept: text/html,...; API clients send application/json or */*.
    return "text/html" in accept


def _render_401(detail: str) -> str:
    safe_detail = escape(detail)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Not authorized · IAP Portal</title>
  <style>
    * {{ box-sizing: border-box; }}
    html, body {{ height: 100%; margin: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI',
                   system-ui, sans-serif;
      -webkit-font-smoothing: antialiased;
      color: #e8e8ee;
      background-color: #07070b;
      background-image:
        radial-gradient(60rem 40rem at 15% -10%, rgba(168, 85, 247, 0.18), transparent 60%),
        radial-gradient(50rem 30rem at 110% 10%, rgba(236, 72, 153, 0.14), transparent 60%),
        radial-gradient(55rem 35rem at 50% 120%, rgba(6, 182, 212, 0.14), transparent 60%);
      background-attachment: fixed;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
    }}
    .card {{
      max-width: 480px;
      width: 100%;
      padding: 32px;
      background: linear-gradient(180deg, rgba(20, 20, 30, 0.7), rgba(10, 10, 18, 0.55));
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 18px;
      backdrop-filter: blur(18px) saturate(160%);
      -webkit-backdrop-filter: blur(18px) saturate(160%);
      box-shadow: 0 20px 60px -20px rgba(0, 0, 0, 0.5);
    }}
    .eyebrow {{
      display: inline-block;
      padding: 4px 10px;
      border-radius: 6px;
      background: rgba(239, 68, 68, 0.12);
      color: #fca5a5;
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      margin-bottom: 14px;
      font-family: 'JetBrains Mono', ui-monospace, monospace;
    }}
    h1 {{ margin: 0 0 8px 0; font-weight: 600; font-size: 24px; letter-spacing: -0.01em; }}
    p {{ color: #8b8b96; margin: 0 0 20px 0; line-height: 1.55; font-size: 14px; }}
    .detail {{
      font-family: 'JetBrains Mono', ui-monospace, monospace;
      font-size: 12px;
      color: #6b6b76;
      background: rgba(255, 255, 255, 0.03);
      padding: 8px 12px;
      border-radius: 6px;
      border: 1px solid rgba(255, 255, 255, 0.05);
      margin-bottom: 20px;
      word-break: break-word;
    }}
    a.btn {{
      display: inline-flex; align-items: center; gap: 6px;
      padding: 10px 18px;
      background-image: linear-gradient(135deg, #7c3aed, #db2777);
      color: white;
      text-decoration: none;
      border-radius: 10px;
      font-weight: 500;
      font-size: 14px;
      box-shadow: 0 6px 24px -6px rgba(168, 85, 247, 0.55);
      transition: transform 0.15s ease;
    }}
    a.btn:hover {{ transform: translateY(-1px); }}
  </style>
</head>
<body>
  <div class="card">
    <div class="eyebrow">401 · Not authorized</div>
    <h1>Sign-in required</h1>
    <p>This app is gated by the IAP Portal. You need an active session before you can access it.</p>
    <div class="detail">{safe_detail}</div>
    <a class="btn" href="/">Go back &rarr;</a>
  </div>
</body>
</html>"""


def unauthorized_response(request: Request, detail: str = "unauthenticated") -> Response:
    """HTML card for browsers, JSON for API clients."""
    if _wants_html(request):
        return HTMLResponse(_render_401(detail), status_code=401)
    return JSONResponse({"detail": detail}, status_code=401)


def install_unauthorized_handler(app: FastAPI) -> None:
    """Register an exception handler that turns HTTPException(401) into an HTML card
    for browsers (JSON for API clients). Other HTTPException statuses pass through
    unchanged."""

    @app.exception_handler(HTTPException)
    async def _handler(request: Request, exc: HTTPException):
        if exc.status_code == 401:
            return unauthorized_response(request, str(exc.detail))
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


class IapPortalAuthMiddleware:
    """Populates `current_user()` context. Optionally enforces auth.

    Pure ASGI, so it covers WebSocket handshakes as well as HTTP requests and
    does not buffer streaming responses. Missing, invalid, or expired tokens
    produce 401 (HTTP) or a rejected handshake (WebSocket) when enforcing.

    Args:
        enforce: if True, missing identity returns 401 (HTML or JSON) instead of
            falling through to the route.
        public_paths: paths that skip auth entirely (exact-match). Useful for
            health probes and liveness checks.
    """

    def __init__(
        self,
        app: ASGIApp,
        enforce: bool = False,
        public_paths: Iterable[str] | None = None,
    ) -> None:
        self.app = app
        self._enforce = enforce
        self._public = set(public_paths or ())

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or scope["path"] in self._public:
            await self.app(scope, receive, send)
            return
        try:
            user = user_from_request(Headers(scope=scope))
        except AuthError as e:
            if not self._enforce:
                await self.app(scope, receive, send)
            elif scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008, "reason": "unauthorized"})
            else:
                await unauthorized_response(Request(scope), str(e))(scope, receive, send)
            return

        token = set_current(user)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_current(token)


def protect(app: FastAPI, *, public_paths: Iterable[str] = ()) -> None:
    """One-liner to wire up portal auth on a FastAPI app.

    Installs:
        - IapPortalAuthMiddleware with enforce=True + public_paths
        - HTML/JSON content-negotiating 401 handler

    After calling this, every route except `public_paths` requires auth, and
    `iap_portal.auth.current_user()` is safe to call inside any protected handler.
    """
    app.add_middleware(
        IapPortalAuthMiddleware,
        enforce=True,
        public_paths=list(public_paths),
    )
    install_unauthorized_handler(app)


__all__ = [
    "IapPortalAuthMiddleware",
    "User",
    "install_unauthorized_handler",
    "protect",
    "require_user",
    "unauthorized_response",
]
