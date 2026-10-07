"""JWKS client with TTL caching, bounded refresh, and stale-key tolerance.

The SDK uses this to verify JWTs minted by the portal. Public keys are fetched
from IAP_PORTAL_JWKS_URL and cached for JWKS_CACHE_SECONDS.

- Unknown `kid`: refetch, but at most once per MIN_REFRESH_SECONDS, so tokens
  with random kids cannot turn into a stream of requests to the portal.
- Portal unreachable: keep using the last good keys for up to MAX_STALE_SECONDS
  and retry after MIN_REFRESH_SECONDS; fail closed after that.
- Only RSA signing keys for RS256 are accepted.
Portal key rotation publishes old and new keys side by side, so a refresh finds
the new kid before any token uses it.
"""

from __future__ import annotations

import os
import threading
import time

import httpx
from jwt import PyJWK, PyJWKSet
from jwt.exceptions import PyJWKSetError

JWKS_CACHE_SECONDS = 300
MIN_REFRESH_SECONDS = 30
MAX_STALE_SECONDS = 3600


class JWKSUnavailable(Exception):
    pass


class JWKSCache:
    def __init__(
        self,
        url: str,
        timeout: float = 3.0,
        *,
        ttl: float = JWKS_CACHE_SECONDS,
        min_refresh: float = MIN_REFRESH_SECONDS,
        max_stale: float = MAX_STALE_SECONDS,
    ) -> None:
        self._url = url
        self._timeout = timeout
        self._ttl = ttl
        self._min_refresh = min_refresh
        self._max_stale = max_stale
        self._lock = threading.Lock()
        self._fetched_at: float = 0.0  # last successful fetch
        self._attempted_at: float = float("-inf")  # last fetch attempt
        self._keys: dict[str, PyJWK] = {}
        self.fetch_count = 0

    def _fetch(self) -> None:
        self._attempted_at = time.monotonic()
        self.fetch_count += 1
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.get(self._url)
                resp.raise_for_status()
                keyset = PyJWKSet.from_dict(resp.json())
        except (httpx.HTTPError, ValueError, PyJWKSetError) as exc:
            raise JWKSUnavailable(str(exc)) from exc
        keys = {}
        for key in keyset.keys:
            usable = key.key_type == "RSA" and key.algorithm_name == "RS256"
            if usable and key.key_id and key.public_key_use in (None, "sig"):
                keys[key.key_id] = key
        self._keys = keys
        self._fetched_at = time.monotonic()

    def _may_refetch(self, now: float) -> bool:
        return now - self._attempted_at >= self._min_refresh

    def get_key(self, kid: str) -> PyJWK:
        with self._lock:
            now = time.monotonic()
            expired = now - self._fetched_at >= self._ttl
            if (expired or kid not in self._keys) and self._may_refetch(now):
                try:
                    self._fetch()
                except JWKSUnavailable:
                    if not self._keys or now - self._fetched_at > self._max_stale:
                        raise
            elif self._keys and now - self._fetched_at > self._max_stale:
                raise JWKSUnavailable("cached portal keys are too old")
            try:
                return self._keys[kid]
            except KeyError:
                if not self._keys and not self._fetched_at:
                    raise JWKSUnavailable("portal keys have not been fetched") from None
                raise KeyError(f"unknown signing key {kid!r}") from None


_default_cache: JWKSCache | None = None


def default_cache() -> JWKSCache:
    global _default_cache
    if _default_cache is None:
        url = os.environ.get(
            "IAP_PORTAL_JWKS_URL", "https://portal.apps.example.com/.well-known/jwks.json"
        )
        _default_cache = JWKSCache(url)
    return _default_cache
