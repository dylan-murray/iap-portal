"""Generic OIDC and Google via Authlib. PKCE, state, and nonce enforced by Authlib."""

from __future__ import annotations

from authlib.integrations.starlette_client import OAuth

from iap_portal_server.config import get_settings

GOOGLE_ISSUER = "https://accounts.google.com"
PROVIDERS = ("oidc", "google")

_oauth = OAuth()
_settings = get_settings()

# The `openid` scope makes Authlib generate a nonce and validate the ID token
# (signature, issuer, audience, expiry, nonce) before exposing token["userinfo"].
if _settings.oidc_issuer:
    _oauth.register(
        name="oidc",
        server_metadata_url=f"{_settings.oidc_issuer.rstrip('/')}/.well-known/openid-configuration",
        client_id=_settings.oidc_client_id,
        client_secret=_settings.oidc_client_secret,
        client_kwargs={
            "scope": " ".join(_settings.oidc_scopes),
            "code_challenge_method": "S256",
            "token_endpoint_auth_method": _settings.oidc_token_endpoint_auth_method,
        },
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
