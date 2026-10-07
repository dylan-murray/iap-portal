from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Settings that earlier releases accepted but no longer honor. Reported as warnings
# so an upgraded deployment notices instead of silently keeping the old behavior.
DEPRECATED_ENV = {
    "PORTAL_COOKIE_DOMAIN": "portal cookies are host-only; apps receive app-scoped sessions",
    "PORTAL_ALLOWED_RETURN_TO_HOSTS": "return_to is limited to the portal and <slug>.<apps domain>",
    "PORTAL_RATE_LIMIT_VERIFY_PER_SECOND": "/auth/verify is not rate limited; denials are deduplicated",
}

LOCAL_HOST_SUFFIXES = (".localhost", ".test")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class Settings(BaseSettings):
    # No implicit .env loading: production settings come from the environment only.
    model_config = SettingsConfigDict(env_prefix="PORTAL_", extra="ignore")

    # production is the default. `dev` allows plain HTTP on local-only hostnames;
    # `test` is used by the automated test suite.
    env: Literal["production", "dev", "test"] = "production"
    # The arbitrary-identity /dev/login endpoint. Requires env=dev as well.
    enable_dev_login: bool = False
    enable_dev_annotations: bool = False
    dev_annotations_dir: Path = Path(".internal/ui-feedback/notes")

    display_name: str = Field(default="iap-portal", min_length=1, max_length=100)

    @field_validator("display_name", mode="before")
    @classmethod
    def _trim_display_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    portal_base_url: str = "https://portal.apps.example.com"
    # Parent domain of app hosts (<slug>.<apps_domain>). Empty: the portal host
    # without its first label (portal.apps.example.com → apps.example.com).
    apps_domain: str = ""
    session_secret: str = Field(..., description="Signs the short-lived OIDC state cookie")

    database_url: str = "postgresql+asyncpg://portal:portal@localhost/portal"
    # Apply Alembic migrations at startup (serialized with a Postgres advisory lock).
    auto_migrate: bool = True

    # Absolute lifetimes. App sessions never outlive their portal session.
    session_ttl_seconds: int = 60 * 60 * 12
    app_session_ttl_seconds: int = 60 * 60 * 12
    app_login_code_ttl_seconds: int = 60

    oidc_display_name: str = Field(default="SSO", min_length=1, max_length=100)
    oidc_scopes: list[str] = Field(default_factory=lambda: ["openid", "email", "profile"])
    oidc_token_endpoint_auth_method: Literal["client_secret_basic", "client_secret_post"] = "client_secret_basic"
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""

    @field_validator("oidc_display_name")
    @classmethod
    def _oidc_display_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("OIDC display name must not be blank")
        return value.strip()

    @field_validator("oidc_scopes")
    @classmethod
    def _oidc_scopes(cls, value: list[str]) -> list[str]:
        if "openid" not in value:
            raise ValueError("OIDC scopes must include openid")
        if any(not scope or any(c.isspace() for c in scope) for scope in value):
            raise ValueError("OIDC scopes must be individual non-empty scope names")
        return list(dict.fromkeys(value))

    google_client_id: str = ""
    google_client_secret: str = ""

    # Sign-in admission. Empty lists admit any verified email from configured IdPs.
    allowed_email_domains: list[str] = Field(default_factory=list)
    # Google Workspace domains required in the `hd` claim for Google sign-in.
    google_hosted_domains: list[str] = Field(default_factory=list)
    # Link a verified email to an existing account whose identities all come from a
    # different issuer (e.g. OIDC and Google Workspace for the same company).
    # Accounts are never re-linked to a different subject from the same issuer.
    link_verified_email_across_issuers: bool = False

    jwt_private_key_pem: str = ""
    jwt_public_key_pem: str = ""
    # File-path alternatives — preferred when keys are mounted as files (compose, k8s secrets).
    jwt_private_key_pem_file: str = ""
    jwt_public_key_pem_file: str = ""
    jwt_kid: str = "portal-v1"
    jwt_ttl_seconds: int = 300
    # Directory of additional verification keys (<kid>.pem) published in JWKS during
    # key rotation. They are never used for signing.
    jwt_additional_public_keys_dir: str = ""

    # Comma-separated list of emails with admin rights. Source of truth for admin
    # status, re-evaluated on every request: removing an email demotes that user as
    # soon as the portal runs with the new configuration.
    admin_emails: str = ""

    # Operator credential for the admin API and CLI. Never distribute it to app
    # namespaces; app registration uses Kubernetes workload identity instead.
    admin_api_token: str = ""

    # App registration with projected Kubernetes ServiceAccount tokens (TokenReview).
    kubernetes_registration_enabled: bool = False
    kubernetes_api_url: str = "https://kubernetes.default.svc"
    kubernetes_token_audience: str = "iap-portal"
    app_namespace_prefix: str = "iap-app-"
    registration_service_account: str = "iap-portal-registration"

    # Number of proxies that append to X-Forwarded-For in front of the portal
    # (the Istio gateway or Compose Envoy = 1). 0 uses the TCP peer address.
    trusted_proxy_hops: int = 1
    rate_limit_login_per_minute: int = 20
    rate_limit_bearer_failures_per_minute: int = 10
    rate_limit_mutations_per_minute: int = 120
    rate_limit_app_login_per_minute: int = 120

    # Deny cross-origin (Origin != the app's own origin) requests to apps at the
    # gateway. Blocks sibling-app CSRF and cross-site WebSocket hijacking.
    enforce_app_same_origin: bool = True

    @field_validator("env", mode="before")
    @classmethod
    def _normalize_env(cls, value: object) -> object:
        if isinstance(value, str) and value.strip().lower() in {"prod", "production"}:
            return "production"
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("allowed_email_domains", "google_hosted_domains")
    @classmethod
    def _lower_domains(cls, value: list[str]) -> list[str]:
        return [d.strip().lower().lstrip("@") for d in value if d.strip()]

    @property
    def admin_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}

    # --- Derived portal/app addressing ---------------------------------------
    @property
    def portal_url_parts(self):
        return urlsplit(self.portal_base_url.strip().rstrip("/"))

    @property
    def portal_scheme(self) -> str:
        return (self.portal_url_parts.scheme or "").lower()

    @property
    def portal_host(self) -> str:
        return (self.portal_url_parts.hostname or "").lower()

    @property
    def portal_port(self) -> int | None:
        """Explicit non-default port of the public portal URL, if any."""
        try:
            port = self.portal_url_parts.port
        except ValueError:
            return None
        default = {"http": 80, "https": 443}.get(self.portal_scheme)
        return None if port == default else port

    @property
    def portal_origin(self) -> str:
        suffix = f":{self.portal_port}" if self.portal_port else ""
        return f"{self.portal_scheme}://{self.portal_host}{suffix}"

    @property
    def issuer(self) -> str:
        return self.portal_origin

    @property
    def resolved_apps_domain(self) -> str:
        if self.apps_domain:
            return self.apps_domain.strip().lower().strip(".")
        return self.portal_host.partition(".")[2]

    @property
    def reserved_slugs(self) -> set[str]:
        reserved = {"portal"}
        label, _, rest = self.portal_host.partition(".")
        if rest == self.resolved_apps_domain:
            reserved.add(label)
        return reserved

    @property
    def secure_cookies(self) -> bool:
        return self.portal_scheme == "https"

    @property
    def dev_login_enabled(self) -> bool:
        return self.env == "dev" and self.enable_dev_login

    def resolved_jwt_private_key_pem(self) -> str:
        """Return the JWT private-key PEM, reading from file if a path was given."""
        if self.jwt_private_key_pem:
            return self.jwt_private_key_pem
        if self.jwt_private_key_pem_file:
            return Path(self.jwt_private_key_pem_file).read_text()
        raise RuntimeError(
            "JWT private key not configured "
            "(set PORTAL_JWT_PRIVATE_KEY_PEM or PORTAL_JWT_PRIVATE_KEY_PEM_FILE)"
        )

    def resolved_jwt_public_key_pem(self) -> str:
        if self.jwt_public_key_pem:
            return self.jwt_public_key_pem
        if self.jwt_public_key_pem_file:
            return Path(self.jwt_public_key_pem_file).read_text()
        raise RuntimeError(
            "JWT public key not configured "
            "(set PORTAL_JWT_PUBLIC_KEY_PEM or PORTAL_JWT_PUBLIC_KEY_PEM_FILE)"
        )


def is_local_hostname(host: str) -> bool:
    return host in LOCAL_HOSTS or host.endswith(LOCAL_HOST_SUFFIXES)


def deprecated_settings_in_env() -> list[str]:
    return [f"{name} is ignored: {why}" for name, why in DEPRECATED_ENV.items() if name in os.environ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
