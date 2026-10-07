"""Python API for the portal — used by the CLI and for scripting."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable

import httpx


@dataclass
class Portal:
    """Portal API client authenticated with a bearer token.

    IAP_PORTAL_TOKEN is the operator credential (PORTAL_ADMIN_API_TOKEN). Keep it
    on operator machines; app namespaces register with workload identity instead.
    """

    base_url: str
    token: str | None = None

    @classmethod
    def from_env(cls) -> "Portal":
        return cls(
            base_url=os.environ.get("IAP_PORTAL_URL", "https://portal.apps.example.com"),
            token=os.environ.get("IAP_PORTAL_TOKEN"),
        )

    def _client(self) -> httpx.Client:
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return httpx.Client(base_url=self.base_url, headers=headers, timeout=30)

    # --- apps ---------------------------------------------------------
    def list_apps(self) -> list[dict]:
        with self._client() as c:
            resp = c.get("/api/apps")
            resp.raise_for_status()
            return resp.json()

    def register_app(
        self,
        *,
        slug: str,
        display_name: str,
        upstream_service: str,
        upstream_port: int = 8080,
        owners: Iterable[str] = (),
        description: str | None = None,
        icon_url: str | None = None,
        health_check_path: str = "/healthz",
    ) -> dict:
        with self._client() as c:
            resp = c.post(
                "/api/apps",
                json={
                    "slug": slug,
                    "display_name": display_name,
                    "description": description,
                    "icon_url": icon_url,
                    "upstream_service": upstream_service,
                    "upstream_port": upstream_port,
                    "health_check_path": health_check_path,
                    "owners": list(owners),
                },
            )
            if resp.status_code == 409:
                # already exists — update instead
                return self.update_app(
                    slug=slug,
                    display_name=display_name,
                    upstream_service=upstream_service,
                    upstream_port=upstream_port,
                    description=description,
                    icon_url=icon_url,
                    health_check_path=health_check_path,
                    owners=owners,
                )
            resp.raise_for_status()
            return resp.json()

    def update_app(self, *, slug: str, **fields) -> dict:
        with self._client() as c:
            resp = c.put(f"/api/apps/{slug}", json={"slug": slug, **fields})
            resp.raise_for_status()
            return resp.json()

    # --- access -------------------------------------------------------
    def grant_access(self, slug: str, *, email: str | None = None, group: str | None = None) -> dict:
        with self._client() as c:
            resp = c.post(
                f"/api/apps/{slug}/access",
                json={"user_email": email, "group_name": group},
            )
            resp.raise_for_status()
            return resp.json()

    # --- users --------------------------------------------------------
    def user_action(self, email: str, action: str) -> dict:
        """action: revoke-sessions | disable | enable | unlink-identities."""
        with self._client() as c:
            if action == "unlink-identities":
                resp = c.delete(f"/api/users/{email}/identities")
            else:
                resp = c.post(f"/api/users/{email}/{action}")
            resp.raise_for_status()
            return resp.json()
