"""Startup configuration checks.

`fatal` problems stop the portal from starting. In production that includes
anything that would weaken authentication; in dev/test only the checks that keep
development features from reaching a non-local deployment.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from iap_portal_server.auth.jwks import key_problems
from iap_portal_server.auth.urls import normalize_host
from iap_portal_server.config import Settings, deprecated_settings_in_env, is_local_hostname

log = logging.getLogger("iap_portal_server.security")

MIN_SECRET_LENGTH = 32
# Values shipped in examples and local tooling.
_SAMPLE_MARKERS = ("change-me", "changeme", "dev-secret", "dev-admin-token", "example")


@dataclass
class ConfigReport:
    fatal: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _looks_like_sample(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _SAMPLE_MARKERS)


def check_settings(s: Settings) -> ConfigReport:
    report = ConfigReport()
    fatal, warn = report.fatal, report.warnings
    production = s.env == "production"

    url = s.portal_url_parts
    if s.portal_scheme not in {"http", "https"} or normalize_host(s.portal_host) is None:
        fatal.append("PORTAL_PORTAL_BASE_URL must be an http(s) URL with a DNS hostname")
    if url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
        fatal.append("PORTAL_PORTAL_BASE_URL must be an origin without path, query, or credentials")
    domain = s.resolved_apps_domain
    if not domain or normalize_host(domain) is None or "." not in domain:
        # Native UI development on localhost has no app hosts; production must.
        (fatal if production else warn).append("apps domain is empty or invalid; set PORTAL_APPS_DOMAIN")
    if len(s.session_secret) < MIN_SECRET_LENGTH:
        fatal.append(f"PORTAL_SESSION_SECRET must be at least {MIN_SECRET_LENGTH} characters")
    if s.enable_dev_annotations and s.env != "dev":
        fatal.append("PORTAL_ENABLE_DEV_ANNOTATIONS requires PORTAL_ENV=dev")
    if s.enable_dev_login and s.env != "dev":
        fatal.append("PORTAL_ENABLE_DEV_LOGIN requires PORTAL_ENV=dev")
    if s.env in {"dev", "test"} and not is_local_hostname(s.portal_host):
        fatal.append(
            "PORTAL_ENV=dev/test is only allowed for localhost, *.localhost, or *.test portal URLs"
        )
    for name, ttl in (
        ("PORTAL_SESSION_TTL_SECONDS", s.session_ttl_seconds),
        ("PORTAL_APP_SESSION_TTL_SECONDS", s.app_session_ttl_seconds),
    ):
        if not 60 <= ttl <= 7 * 24 * 3600:
            fatal.append(f"{name} must be between 60 seconds and 7 days")
    if not 30 <= s.jwt_ttl_seconds <= 3600:
        fatal.append("PORTAL_JWT_TTL_SECONDS must be between 30 and 3600")

    if production:
        if s.portal_scheme != "https":
            fatal.append("production requires an https PORTAL_PORTAL_BASE_URL")
        if _looks_like_sample(s.session_secret):
            fatal.append("PORTAL_SESSION_SECRET is a sample value")
        if s.admin_api_token and (
            len(s.admin_api_token) < MIN_SECRET_LENGTH or _looks_like_sample(s.admin_api_token)
        ):
            fatal.append(
                f"PORTAL_ADMIN_API_TOKEN must be a random value of at least {MIN_SECRET_LENGTH} characters"
            )
        if not (s.okta_issuer or s.google_client_id):
            fatal.append("at least one identity provider must be configured")
        fatal.extend(key_problems(s))
    else:
        warn.extend(key_problems(s))

    if s.google_client_id and not (s.google_hosted_domains or s.allowed_email_domains):
        warn.append(
            "Google sign-in admits any verified Google account; set PORTAL_GOOGLE_HOSTED_DOMAINS "
            "or PORTAL_ALLOWED_EMAIL_DOMAINS unless external accounts are intended"
        )
    warn.extend(deprecated_settings_in_env())
    return report


def enforce_settings(s: Settings) -> None:
    report = check_settings(s)
    for message in report.warnings:
        log.warning("configuration: %s", message)
    if report.fatal:
        raise RuntimeError("Unsafe portal configuration: " + "; ".join(report.fatal))
