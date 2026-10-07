"""App registration with Kubernetes workload identity.

The iap-app chart's registration Job presents a projected ServiceAccount token
(audience `iap-portal`, minutes-long lifetime). The portal validates it with the
Kubernetes TokenReview API and maps the identity to exactly one app:

    system:serviceaccount:iap-app-<slug>:iap-portal-registration  →  <slug>

Namespace names are immutable and created by the platform operator, so an app
deployer can only obtain tokens that register their own app. The portal's
ServiceAccount needs the `system:auth-delegator` ClusterRole.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from iap_portal_server.auth.urls import is_valid_slug
from iap_portal_server.config import Settings, get_settings

SERVICE_ACCOUNT_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")


class TokenReviewUnavailable(RuntimeError):
    pass


def looks_like_jwt(token: str) -> bool:
    parts = token.split(".")
    return len(parts) == 3 and all(parts) and len(token) < 16384


async def review_token(token: str, settings: Settings | None = None) -> str | None:
    """Username for an authenticated token with our audience, else None."""
    settings = settings or get_settings()
    audience = settings.kubernetes_token_audience
    try:
        own_token = (SERVICE_ACCOUNT_DIR / "token").read_text().strip()
        async with httpx.AsyncClient(
            verify=str(SERVICE_ACCOUNT_DIR / "ca.crt"), timeout=5.0
        ) as client:
            response = await client.post(
                f"{settings.kubernetes_api_url.rstrip('/')}/apis/authentication.k8s.io/v1/tokenreviews",
                json={
                    "apiVersion": "authentication.k8s.io/v1",
                    "kind": "TokenReview",
                    "spec": {"token": token, "audiences": [audience]},
                },
                headers={"Authorization": f"Bearer {own_token}"},
            )
            response.raise_for_status()
    except (OSError, httpx.HTTPError) as exc:
        raise TokenReviewUnavailable(str(exc)) from exc
    status = response.json().get("status") or {}
    if status.get("authenticated") is not True or audience not in (status.get("audiences") or []):
        return None
    return (status.get("user") or {}).get("username")


def registration_slug(username: str | None, settings: Settings | None = None) -> str | None:
    """The single app a ServiceAccount username may register, or None."""
    settings = settings or get_settings()
    parts = (username or "").split(":")
    if len(parts) != 4 or parts[:2] != ["system", "serviceaccount"]:
        return None
    namespace, account = parts[2], parts[3]
    prefix = settings.app_namespace_prefix
    if account != settings.registration_service_account or not namespace.startswith(prefix):
        return None
    slug = namespace[len(prefix):]
    return slug if is_valid_slug(slug, settings) else None
