"""Host and redirect policy shared by ext_authz, login, and app sign-in.

Every decision here is made from the configured portal URL and apps domain.
Request headers only supply the value being checked, never the policy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import HTTPException

from iap_portal_server.config import Settings, get_settings

SLUG_PATTERN = r"^[a-z][a-z0-9-]{0,61}[a-z0-9]$"
_SLUG_RE = re.compile(SLUG_PATTERN)
_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
# Path characters we are willing to hand back to a browser in Location.
_UNSAFE_PATH_RE = re.compile(r"[\x00-\x20\x7f\\]")


def normalize_host(raw: str | None) -> str | None:
    """Lowercase DNS hostname without port, or None if malformed.

    Rejects IPv6 literals, userinfo, trailing dots, empty labels, and anything
    outside the LDH character set. Ports are validated and dropped: app identity
    is the hostname, and the gateway routes on the hostname.
    """
    if not raw or len(raw) > 300:
        return None
    raw = raw.strip()
    host, sep, port = raw.rpartition(":")
    if not sep:
        host, port = raw, ""
    elif not port.isdigit() or not 0 < int(port) < 65536:
        return None
    host = host.lower()
    if not host or len(host) > 253 or host.endswith("."):
        return None
    if not all(_LABEL_RE.match(label) for label in host.split(".")):
        return None
    return host


def is_valid_slug(slug: str, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return bool(_SLUG_RE.match(slug)) and slug not in settings.reserved_slugs


def app_slug_for_host(host: str | None, settings: Settings | None = None) -> str | None:
    """Slug for an exact `<slug>.<apps_domain>` host; None for anything else."""
    settings = settings or get_settings()
    if host is None or host == settings.portal_host:
        return None
    suffix = "." + settings.resolved_apps_domain
    if not host.endswith(suffix):
        return None
    slug = host[: -len(suffix)]
    return slug if is_valid_slug(slug, settings) else None


def _port_suffix(settings: Settings) -> str:
    return f":{settings.portal_port}" if settings.portal_port else ""


def app_origin(slug: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    return f"{settings.portal_scheme}://{slug}.{settings.resolved_apps_domain}{_port_suffix(settings)}"


def portal_url(path: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    return settings.portal_origin + path


def safe_path(value: str) -> str | None:
    """A same-origin path+query safe to place in Location, or None."""
    if not value.startswith("/") or value.startswith("//") or _UNSAFE_PATH_RE.search(value):
        return None
    parts = urlsplit(value)
    if parts.scheme or parts.netloc:
        return None
    return value


@dataclass(frozen=True)
class RedirectTarget:
    """A validated redirect: portal-relative (slug None) or on an app host."""

    slug: str | None
    path: str

    def url(self, settings: Settings | None = None) -> str:
        if self.slug is None:
            return portal_url(self.path, settings)
        return app_origin(self.slug, settings) + self.path


def parse_redirect(url: str, settings: Settings | None = None) -> RedirectTarget | None:
    """Parse a return_to/rd value against the configured origins.

    Accepts a portal-relative path, or an absolute URL whose scheme and port match
    the portal and whose host is the portal or `<slug>.<apps_domain>`. Userinfo,
    backslashes, control characters, and scheme-relative forms are rejected; the
    result is rebuilt from validated parts so browser and server parse it the same.
    """
    settings = settings or get_settings()
    if not isinstance(url, str) or len(url) > 4096:
        return None
    if url.startswith("/"):
        path = safe_path(url)
        return RedirectTarget(None, path) if path else None
    if _UNSAFE_PATH_RE.search(url):
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() != settings.portal_scheme or "@" in parts.netloc:
        return None
    default = {"http": 80, "https": 443}.get(settings.portal_scheme)
    if (None if port == default else port) != settings.portal_port:
        return None
    host = normalize_host(parts.hostname)
    if host is None:
        return None
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    path = safe_path(path)
    if path is None:
        return None
    if host == settings.portal_host:
        return RedirectTarget(None, path)
    slug = app_slug_for_host(host, settings)
    return RedirectTarget(slug, path) if slug else None


def validate_return_to(url: str) -> str:
    """Open-redirect protection for `return_to`. Returns a canonical URL or path."""
    target = parse_redirect(url)
    if target is None:
        raise HTTPException(400, "return_to is not an allowed portal or app URL")
    return target.path if target.slug is None else target.url()
