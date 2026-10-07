"""Framework-agnostic auth core.

Apps consume identity in one of three modes:

  1. **JWT verify** (prod default) — verify the Bearer JWT the gateway injects
     against portal JWKS: RS256 only, `aud` = this app's slug, `iss` = the portal.
  2. **Header trust** — read X-Forwarded-Email etc. Relies on the gateway
     replacing those headers and on nothing else reaching the app.
  3. **Dev fake** — when IAP_PORTAL_DEV=1 is set, return a configurable fake User
     without any network call. Used by `iap-portal dev` for fast local loops.
     Refused inside Kubernetes.

Env knobs:
    IAP_PORTAL_APP_SLUG=...              — expected audience (required to verify)
    IAP_PORTAL_ISSUER=...                — expected issuer, the portal URL (required to verify)
    IAP_PORTAL_JWKS_URL=...              — where to fetch portal public keys
    IAP_PORTAL_VERIFY_JWT=true|false     (default true) — enforce JWT verification
    IAP_PORTAL_MAX_CONNECTION_AGE_SECONDS (default 3600) — see below
    IAP_PORTAL_DEV=1                     — fake-user short-circuit for local dev
    IAP_PORTAL_DEV_EMAIL=...             — dev fake email       (default you@example.com)
    IAP_PORTAL_DEV_NAME=...              — dev fake display name
    IAP_PORTAL_DEV_GROUPS=a,b,c          — dev fake groups      (default engineering)

Long-lived connections: portal JWTs expire after minutes. Ordinary requests get
a fresh token from the gateway every time. A Streamlit session only has the
token from its WebSocket handshake, which the gateway authorized when the
connection opened. That token is accepted past `exp` for at most
IAP_PORTAL_MAX_CONNECTION_AGE_SECONDS after it was issued; then the user must
reload, which re-runs gateway authorization (session revocation, live grants).
"""

from __future__ import annotations

import contextvars
import os
import time
from dataclasses import dataclass, field
from typing import Mapping

import jwt as pyjwt

from iap_portal.auth.jwks import JWKSCache, JWKSUnavailable, default_cache

ALGORITHM = "RS256"
LEEWAY_SECONDS = 30
DEFAULT_MAX_CONNECTION_AGE = 3600


@dataclass(frozen=True)
class User:
    email: str
    name: str | None = None
    groups: list[str] = field(default_factory=list)
    user_id: str | None = None
    raw_claims: dict | None = None

    def in_group(self, group: str) -> bool:
        return group in self.groups


# Per-request context (set by framework adapters).
_current_user_var: contextvars.ContextVar[User | None] = contextvars.ContextVar(
    "iap_portal_current_user", default=None
)


class AuthError(Exception):
    pass


class MissingIdentity(AuthError):
    pass


class InvalidToken(AuthError):
    pass


class TokenExpired(InvalidToken):
    """The token (or the connection it authorized) is too old; reauthenticate."""


def _verify_enabled() -> bool:
    return os.environ.get("IAP_PORTAL_VERIFY_JWT", "true").lower() not in {"0", "false", "no"}


def _dev_mode_enabled() -> bool:
    if os.environ.get("IAP_PORTAL_DEV", "").lower() not in {"1", "true", "yes"}:
        return False
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        # A fake identity must never serve real traffic.
        raise AuthError("IAP_PORTAL_DEV is not allowed inside Kubernetes")
    return True


def _dev_user() -> User:
    groups_raw = os.environ.get("IAP_PORTAL_DEV_GROUPS", "engineering")
    return User(
        email=os.environ.get("IAP_PORTAL_DEV_EMAIL", "you@example.com"),
        name=os.environ.get("IAP_PORTAL_DEV_NAME") or "You",
        groups=[g.strip() for g in groups_raw.split(",") if g.strip()],
        user_id=os.environ.get("IAP_PORTAL_DEV_USER_ID", "dev-user"),
    )


def max_connection_age() -> int:
    raw = os.environ.get("IAP_PORTAL_MAX_CONNECTION_AGE_SECONDS", "")
    try:
        value = int(raw) if raw else DEFAULT_MAX_CONNECTION_AGE
    except ValueError:
        value = DEFAULT_MAX_CONNECTION_AGE
    return max(0, value)


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise InvalidToken(f"{name} is not set; cannot verify portal tokens")
    return value


def verify_jwt(
    token: str,
    cache: JWKSCache | None = None,
    *,
    connection_max_age: int | None = None,
) -> User:
    """Verify a Bearer JWT minted by the portal. Raises InvalidToken on failure.

    `connection_max_age` accepts an expired token as long as it was issued at
    most that many seconds ago. Use it only for a token that authorized a
    still-open connection (see module docs), never for per-request tokens.
    """
    audience = _required_env("IAP_PORTAL_APP_SLUG")
    issuer = _required_env("IAP_PORTAL_ISSUER").rstrip("/")
    cache = cache or default_cache()
    try:
        header = pyjwt.get_unverified_header(token)
        if header.get("alg") != ALGORITHM:
            raise InvalidToken("unexpected JWT algorithm")
        kid = header.get("kid")
        if not kid or not isinstance(kid, str):
            raise InvalidToken("JWT missing kid")
        signing_key = cache.get_key(kid).key
        claims = pyjwt.decode(
            token,
            signing_key,
            algorithms=[ALGORITHM],
            audience=audience,
            issuer=issuer,
            leeway=LEEWAY_SECONDS,
            options={
                "require": ["exp", "iat", "sub", "aud", "iss"],
                "verify_exp": connection_max_age is None,
            },
        )
    except pyjwt.ExpiredSignatureError as e:
        raise TokenExpired("token expired") from e
    except (pyjwt.PyJWTError, KeyError) as e:
        raise InvalidToken(str(e)) from e
    except JWKSUnavailable as e:
        raise InvalidToken(f"portal keys unavailable: {e}") from e
    if connection_max_age is not None:
        issued = claims.get("iat")
        if not isinstance(issued, (int, float)) or time.time() - issued > connection_max_age + LEEWAY_SECONDS:
            raise TokenExpired("connection is older than the maximum allowed age")
    email = claims.get("email")
    if not isinstance(email, str) or not email:
        raise InvalidToken("token has no email")
    return User(
        email=email,
        name=claims.get("name"),
        groups=list(claims.get("groups") or []),
        user_id=str(claims["sub"]),
        raw_claims=claims,
    )


def user_from_headers(headers: Mapping[str, str]) -> User | None:
    email = headers.get("x-forwarded-email") or headers.get("X-Forwarded-Email")
    if not email:
        return None
    name = headers.get("x-forwarded-name") or headers.get("X-Forwarded-Name") or None
    raw_groups = headers.get("x-forwarded-groups") or headers.get("X-Forwarded-Groups") or ""
    groups = [g for g in raw_groups.split(",") if g]
    user_id = headers.get("x-forwarded-user") or headers.get("X-Forwarded-User")
    return User(email=email, name=name, groups=groups, user_id=user_id)


def _auth_header(headers: Mapping[str, str]) -> str | None:
    raw = headers.get("authorization") or headers.get("Authorization")
    if not raw:
        return None
    scheme, _, token = raw.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


def user_from_request(
    headers: Mapping[str, str], *, connection_max_age: int | None = None
) -> User:
    """Main entry point used by framework adapters and `current_user()`.

    Resolution order:
      1. IAP_PORTAL_DEV=1 → return fake dev user (no network, no headers needed)
      2. Signed JWT on Authorization header (if verification enabled)
      3. X-Forwarded-* headers (if verification disabled)
    Raises MissingIdentity when none apply and InvalidToken for a bad token.
    """
    if _dev_mode_enabled():
        return _dev_user()

    if _verify_enabled():
        token = _auth_header(headers)
        if token is None:
            raise MissingIdentity("no Authorization: Bearer token on request")
        return verify_jwt(token, connection_max_age=connection_max_age)

    hu = user_from_headers(headers)
    if hu is None:
        raise MissingIdentity("no X-Forwarded-Email header on request")
    return hu


def set_current(user: User) -> contextvars.Token:
    return _current_user_var.set(user)


def reset_current(token: contextvars.Token) -> None:
    _current_user_var.reset(token)


def current_user() -> User:
    """Return the user for the current context.

    In frameworks that set context per request (FastAPI/Flask adapters), this works
    directly in route handlers. In Streamlit, we read `st.context.headers` (the
    WebSocket handshake) with the bounded connection age. For Gradio, pass the
    `gr.Request` via `user_from_request(req.headers)`.
    IAP_PORTAL_DEV=1 short-circuits all of this and returns a fake user.
    """
    if _dev_mode_enabled():
        return _dev_user()

    u = _current_user_var.get()
    if u is not None:
        return u

    headers = _discover_streamlit_headers()
    if headers is None:
        raise MissingIdentity(
            "no current-user context. Use the FastAPI/Flask adapter, call "
            "user_from_request(headers) explicitly (Gradio), or ensure Streamlit "
            "exposes st.context.headers. For local dev, set IAP_PORTAL_DEV=1 "
            "(or use `iap-portal dev`)."
        )
    return user_from_request(headers, connection_max_age=max_connection_age())


def _discover_streamlit_headers() -> Mapping[str, str] | None:
    """Best-effort Streamlit header discovery — returns None if unavailable."""
    try:
        import streamlit as st  # type: ignore  # optional dependency
    except ImportError:
        return None
    ctx = getattr(st, "context", None)
    if ctx is None:
        return None
    headers = getattr(ctx, "headers", None)
    if headers is None:
        return None
    try:
        return dict(headers)
    except Exception:
        return None
