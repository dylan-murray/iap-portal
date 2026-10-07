"""Portal ASGI middleware.

Implemented as pure ASGI middleware (not BaseHTTPMiddleware) so they don't buffer
or wrap streaming responses — BaseHTTPMiddleware is known to corrupt binary
Content-Length responses from StaticFiles (see encode/starlette#1438).
"""

from __future__ import annotations

import json

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from iap_portal_server.auth.session import portal_cookie_name
from iap_portal_server.config import get_settings

_STRIPPED_HEADERS = {
    b"x-forwarded-email",
    b"x-forwarded-user",
    b"x-forwarded-groups",
    b"x-forwarded-name",
    b"x-iap-portal-app",
}

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class StripInboundIdentityMiddleware:
    """Drop client-supplied identity headers before any portal handler sees them."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            scope = dict(scope)
            scope["headers"] = [
                (k, v)
                for (k, v) in scope["headers"]
                if k.lower() not in _STRIPPED_HEADERS
            ]
        await self.app(scope, receive, send)


class OriginCheckMiddleware:
    """CSRF defense for cookie-authenticated mutations.

    Unsafe requests that carry the portal session cookie must come from the portal
    origin: `Origin` must equal it, or, when a browser omits Origin,
    `Sec-Fetch-Site` must be `same-origin`. Requests without the session cookie
    (CLI and service callers using bearer tokens) are unaffected. SameSite=Lax
    alone does not help here: sibling app subdomains are same-site.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["method"] in _UNSAFE_METHODS
        ):
            headers = Headers(scope=scope)
            if _has_cookie(headers.get("cookie", ""), portal_cookie_name()) and not _same_origin(headers):
                await _reject(send)
                return
        await self.app(scope, receive, send)


def _has_cookie(cookie_header: str, name: str) -> bool:
    return any(part.split("=", 1)[0].strip() == name for part in cookie_header.split(";"))


def _same_origin(headers: Headers) -> bool:
    origin = headers.get("origin")
    if origin is not None:
        return origin == get_settings().portal_origin
    return headers.get("sec-fetch-site") == "same-origin"


async def _reject(send: Send) -> None:
    body = json.dumps({"detail": "cross-origin request rejected"}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class SecurityHeadersMiddleware:
    """Framing, sniffing, and referrer protection for portal responses."""

    _HEADERS = [
        (b"x-frame-options", b"DENY"),
        (b"x-content-type-options", b"nosniff"),
        (b"referrer-policy", b"same-origin"),
    ]

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = {k.lower() for k, _ in message.get("headers", [])}
                extra = [(k, v) for k, v in self._HEADERS if k not in existing]
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        await self.app(scope, receive, send_with_headers)


__all__ = ["OriginCheckMiddleware", "SecurityHeadersMiddleware", "StripInboundIdentityMiddleware"]
