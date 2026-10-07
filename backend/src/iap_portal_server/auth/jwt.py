"""Mint short-lived RS256 JWTs that downstream apps verify via JWKS."""

from __future__ import annotations

import time

import jwt

from iap_portal_server.auth.jwks import signing_key
from iap_portal_server.config import get_settings


def mint_for_app(
    *, user_id: int, email: str, name: str | None, groups: list[str], app_slug: str
) -> str:
    """Token for one app: `aud` is the app slug, `sub` the stable portal user id."""
    s = get_settings()
    now = int(time.time())
    payload = {
        "iss": s.issuer,
        "sub": str(user_id),
        "aud": app_slug,
        "email": email,
        "name": name,
        "groups": groups,
        "iat": now,
        "exp": now + s.jwt_ttl_seconds,
    }
    return jwt.encode(payload, signing_key(s), algorithm="RS256", headers={"kid": s.jwt_kid})
