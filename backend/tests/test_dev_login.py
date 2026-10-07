"""Dev login — exists only with PORTAL_ENV=dev AND PORTAL_ENABLE_DEV_LOGIN=true."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from iap_portal_server.auth.session import portal_cookie_name
from iap_portal_server.config import get_settings
from iap_portal_server.db.models import Group, GroupMembership, User
from iap_portal_server.db.session import get_db as real_get_db
from iap_portal_server.main import create_app
from tests.conftest import PORTAL


async def _client(_fresh_db):
    app = create_app()

    async def _override():
        async with _fresh_db() as s:
            yield s

    app.dependency_overrides[real_get_db] = _override
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url=PORTAL,
        headers={"origin": PORTAL},
    )


@pytest.fixture
async def dev_client(_fresh_db, monkeypatch):
    monkeypatch.setenv("PORTAL_ENV", "dev")
    monkeypatch.setenv("PORTAL_ENABLE_DEV_LOGIN", "true")
    get_settings.cache_clear()
    async with await _client(_fresh_db) as c:
        yield c
    get_settings.cache_clear()


async def test_dev_login_with_a_session_cookie_still_requires_same_origin(dev_client):
    first = await dev_client.post("/dev/login", json={"email": "alice@example.com"})
    assert first.status_code == 200
    resp = await dev_client.post(
        "/dev/login", json={"email": "mallory@example.com"}, headers={"origin": "http://evil.iapportal.test"}
    )
    assert resp.status_code == 403


def _has_dev_route(app) -> bool:
    return "/dev/login" in app.openapi()["paths"]


async def test_dev_login_not_registered_by_default(client):
    assert not _has_dev_route(create_app())
    resp = await client.post("/dev/login", json={"email": "alice@example.com"})
    assert resp.status_code in (404, 405)  # 405 when the SPA catch-all is mounted


async def test_dev_env_alone_does_not_enable_dev_login(_fresh_db, monkeypatch):
    monkeypatch.setenv("PORTAL_ENV", "dev")
    get_settings.cache_clear()
    try:
        assert not _has_dev_route(create_app())
        async with await _client(_fresh_db) as c:
            assert (await c.post("/dev/login", json={"email": "alice@example.com"})).status_code in (404, 405)
    finally:
        get_settings.cache_clear()


async def test_dev_login_flag_outside_dev_refuses_to_start(monkeypatch):
    monkeypatch.setenv("PORTAL_ENV", "test")
    monkeypatch.setenv("PORTAL_ENABLE_DEV_LOGIN", "true")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="PORTAL_ENABLE_DEV_LOGIN"):
            create_app()
    finally:
        get_settings.cache_clear()


async def test_dev_login_route_exists_only_when_enabled(dev_client):
    assert _has_dev_route(create_app())


async def test_dev_login_creates_user_and_server_side_session(dev_client, _fresh_db):
    resp = await dev_client.post(
        "/dev/login",
        json={"email": "alice@example.com", "name": "Alice", "groups": ["engineering", "admins"]},
    )
    assert resp.status_code == 200
    assert portal_cookie_name() in resp.cookies
    me = await dev_client.get("/api/me", headers={"cookie": f"{portal_cookie_name()}={resp.cookies[portal_cookie_name()]}"})
    assert me.json()["email"] == "alice@example.com"

    async with _fresh_db() as db:
        user = (await db.execute(select(User).where(User.email == "alice@example.com"))).scalar_one()
        assert user.name == "Alice"
        group_names = {
            r[0]
            for r in (
                await db.execute(
                    select(Group.name)
                    .join(GroupMembership, GroupMembership.group_id == Group.id)
                    .where(GroupMembership.user_id == user.id)
                )
            ).all()
        }
        assert group_names == {"engineering", "admins"}


async def test_dev_login_is_idempotent(dev_client, _fresh_db):
    for _ in range(3):
        resp = await dev_client.post("/dev/login", json={"email": "alice@example.com", "groups": ["engineering"]})
        assert resp.status_code == 200

    async with _fresh_db() as db:
        users = (await db.execute(select(User).where(User.email == "alice@example.com"))).scalars().all()
        assert len(users) == 1
