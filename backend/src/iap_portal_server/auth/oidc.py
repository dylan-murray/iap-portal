"""OIDC via Authlib for Okta + Google. PKCE, state, and nonce enforced by Authlib."""

from __future__ import annotations

from authlib.integrations.starlette_client import OAuth

from iap_portal_server.config import get_settings

GOOGLE_ISSUER = "https://accounts.google.com"
PROVIDERS = ("okta", "google")

_oauth = OAuth()
_settings = get_settings()

# The `openid` scope makes Authlib generate a nonce and validate the ID token
# (signature, issuer, audience, expiry, nonce) before exposing token["userinfo"].
if _settings.okta_issuer:
    _oauth.register(
        name="okta",
        server_metadata_url=f"{_settings.okta_issuer.rstrip('/')}/.well-known/openid-configuration",
        client_id=_settings.okta_client_id,
        client_secret=_settings.okta_client_secret,
        client_kwargs={"scope": "openid email profile groups", "code_challenge_method": "S256"},
    )

if _settings.google_client_id:
    _oauth.register(
        name="google",
        server_metadata_url=f"{GOOGLE_ISSUER}/.well-known/openid-configuration",
        client_id=_settings.google_client_id,
        client_secret=_settings.google_client_secret,
        client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"},
    )


def oauth() -> OAuth:
    return _oauth


async def provider_issuer(client) -> str:
    """The issuer the provider's discovery document declares."""
    metadata = await client.load_server_metadata()
    return str(metadata.get("issuer") or "").rstrip("/")
