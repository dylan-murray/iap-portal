"""The private auth router must never be reachable on the public application."""
import pytest

from .conftest import app_session, check, make_app, make_user, portal_session


@pytest.mark.parametrize("path", ["/auth/verify", "/auth/verify/", "/auth/verify/nested"])
async def test_public_listener_does_not_issue_identity(client, authorization_client, db, path):
    user = await make_user(db, "owner@example.com")
    await make_app(db, "demo", owner=user)
    _, parent = await portal_session(db, user)
    cookie = await app_session(db, parent, "demo")
    host = "demo.iapportal.test:8080"
    allowed = await check(authorization_client, host, cookie=cookie)
    assert allowed.status_code == 200
    for method in ("GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"):
        response = await client.request(method, path, headers={"host": host, "cookie": cookie})
        assert response.status_code in (404, 405)
        assert "authorization" not in response.headers


@pytest.mark.parametrize("path", ["/api/me", "/login", "/.well-known/jwks.json", "/docs", "/openapi.json"])
async def test_private_listener_has_no_portal_api(authorization_client, path):
    assert (await authorization_client.get(path)).status_code == 404


async def test_public_jwks_and_private_health_remain_available(client, authorization_client):
    assert (await client.get("/.well-known/jwks.json")).status_code == 200
    assert (await authorization_client.get("/healthz")).status_code == 200
