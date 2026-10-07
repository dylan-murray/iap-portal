"""Portal/app session separation: app sign-in codes, cookies, logout, revocation."""

from __future__ import annotations

from datetime import timedelta
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, quote, urlsplit

import pytest
from sqlalchemy import select

from iap_portal_server.auth.session import app_cookie_name, portal_cookie_name, utcnow
from iap_portal_server.db.models import AppLoginCode
from tests.conftest import PORTAL, check, make_app, make_user, portal_session

APP = "http://annotation.iapportal.test:8080"
APP_HOST = "annotation.iapportal.test:8080"


def _cookie(set_cookie: str) -> SimpleCookie:
    jar = SimpleCookie()
    jar.load(set_cookie)
    return jar


async def _start(client, cookie: str, rd: str):
    return await client.get(f"/auth/app-login?rd={quote(rd, safe='')}", headers={"cookie": cookie})


async def _sign_into_app(client, authorization_client, cookie: str, rd: str = APP + "/label/42?x=1&y=2"):
    start = await _start(client, cookie, rd)
    assert start.status_code == 302, start.text
    callback = urlsplit(start.headers["location"])
    assert f"{callback.scheme}://{callback.netloc}" == APP
    assert callback.path == "/.iap-portal/callback"
    done = await check(authorization_client, APP_HOST, f"{callback.path}?{callback.query}")
    return start, done


async def test_full_app_sign_in_sets_host_only_app_cookie(client, db, authorization_client):
    user = await make_user(db, "owner@example.com")
    await make_app(db, "annotation", owner=user)
    cookie, _ = await portal_session(db, user)
    start, done = await _sign_into_app(client, authorization_client, cookie)
    assert start.headers["cache-control"] == "no-store"
    assert done.status_code == 302
    assert done.headers["location"] == APP + "/label/42?x=1&y=2"
    jar = _cookie(done.headers["set-cookie"])
    morsel = jar[app_cookie_name()]
    assert morsel["domain"] == ""  # host-only: never sent to sibling apps or the portal
    assert morsel["httponly"] and morsel["samesite"].lower() == "lax" and morsel["path"] == "/"
    app_cookie = f"{app_cookie_name()}={morsel.value}"
    assert (await check(authorization_client, APP_HOST, cookie=app_cookie)).status_code == 200


async def test_codes_are_single_use_and_bound_to_their_app(client, db, authorization_client):
    user = await make_user(db, "owner@example.com")
    await make_app(db, "annotation", owner=user)
    await make_app(db, "other", owner=user)
    cookie, _ = await portal_session(db, user)
    start = await _start(client, cookie, APP + "/")
    query = urlsplit(start.headers["location"]).query
    other = await check(authorization_client, "other.iapportal.test:8080", f"/.iap-portal/callback?{query}")
    assert "set-cookie" not in other.headers
    first = await check(authorization_client, APP_HOST, f"/.iap-portal/callback?{query}")
    assert "set-cookie" in first.headers
    replay = await check(authorization_client, APP_HOST, f"/.iap-portal/callback?{query}")
    assert "set-cookie" not in replay.headers and replay.headers["location"].startswith(PORTAL)


async def test_expired_code_is_rejected(client, db, authorization_client):
    user = await make_user(db, "owner@example.com")
    await make_app(db, "annotation", owner=user)
    cookie, _ = await portal_session(db, user)
    start = await _start(client, cookie, APP + "/")
    row = (await db.execute(select(AppLoginCode))).scalar_one()
    row.expires_at = utcnow() - timedelta(seconds=1)
    await db.commit()
    query = urlsplit(start.headers["location"]).query
    done = await check(authorization_client, APP_HOST, f"/.iap-portal/callback?{query}")
    assert "set-cookie" not in done.headers


async def test_code_for_logged_out_session_does_not_create_app_session(client, db, authorization_client):
    user = await make_user(db, "owner@example.com")
    await make_app(db, "annotation", owner=user)
    cookie, _ = await portal_session(db, user)
    start = await _start(client, cookie, APP + "/")
    await client.post("/logout", headers={"cookie": cookie, "origin": PORTAL})
    query = urlsplit(start.headers["location"]).query
    done = await check(authorization_client, APP_HOST, f"/.iap-portal/callback?{query}")
    assert "set-cookie" not in done.headers


async def test_app_login_without_portal_session_goes_to_sign_in_and_keeps_deep_link(client, db):
    await make_app(db, "annotation")
    rd = APP + "/label/42?x=1&y=%2F"
    resp = await client.get(f"/auth/app-login?rd={quote(rd, safe='')}")
    assert resp.status_code == 302
    return_to = parse_qs(urlsplit(resp.headers["location"]).query)["return_to"][0]
    assert parse_qs(urlsplit(return_to).query)["rd"][0] == rd
    page = await client.get(resp.headers["location"])
    assert page.status_code == 200
    # The provider link must carry return_to URL-encoded so nested queries survive.
    assert f"/login/google?return_to={quote(return_to, safe='')}" in page.text


@pytest.mark.parametrize(
    "rd",
    ["https://evil.example.com/", PORTAL + "/", "http://evil@annotation.iapportal.test:8080/",
     "https://annotation.iapportal.test/", "http://annotation.iapportal.test:9999/",
     "http://a.b.iapportal.test:8080/", "/relative", "javascript:alert(1)",
     "http://annotation.iapportal.test:8080/\\evil.example.com"],
)
async def test_app_login_rejects_non_app_targets(client, db, rd):
    user = await make_user(db, "owner@example.com")
    cookie, _ = await portal_session(db, user)
    assert (await _start(client, cookie, rd)).status_code == 400


async def test_unknown_app_gets_no_code(client, db):
    user = await make_user(db, "owner@example.com")
    cookie, _ = await portal_session(db, user)
    assert (await _start(client, cookie, "http://ghost.iapportal.test:8080/")).status_code == 404


async def test_logout_revokes_portal_and_app_sessions(client, db, authorization_client):
    user = await make_user(db, "owner@example.com")
    await make_app(db, "annotation", owner=user)
    cookie, _ = await portal_session(db, user)
    _, done = await _sign_into_app(client, authorization_client, cookie)
    app_cookie = f"{app_cookie_name()}={_cookie(done.headers['set-cookie'])[app_cookie_name()].value}"
    assert (await client.get("/api/me", headers={"cookie": cookie})).status_code == 200

    out = await client.post("/logout", headers={"cookie": cookie, "origin": PORTAL})
    assert out.status_code == 303
    assert portal_cookie_name() in out.headers["set-cookie"]
    assert (await client.get("/api/me", headers={"cookie": cookie})).status_code == 401
    assert (await check(authorization_client, APP_HOST, cookie=app_cookie)).status_code == 302


async def test_get_logout_requires_same_origin_navigation(client, db):
    user = await make_user(db, "owner@example.com")
    cookie, _ = await portal_session(db, user)
    for site in (None, "same-site", "cross-site"):
        headers = {"cookie": cookie, **({"sec-fetch-site": site} if site else {})}
        page = await client.get("/logout", headers=headers)
        assert page.status_code == 200 and 'method="post"' in page.text
        assert (await client.get("/api/me", headers={"cookie": cookie})).status_code == 200
    done = await client.get("/logout", headers={"cookie": cookie, "sec-fetch-site": "same-origin"})
    assert done.status_code == 303
    assert (await client.get("/api/me", headers={"cookie": cookie})).status_code == 401


async def test_portal_cookie_attributes_over_https(client, db, settings, authorization_client):
    settings.set(portal_base_url="https://portal.iapportal.test")
    assert portal_cookie_name() == "__Host-iap_portal_session"
    assert app_cookie_name() == "__Host-iap_app_session"
    user = await make_user(db, "owner@example.com")
    await make_app(db, "annotation", owner=user)
    cookie, _ = await portal_session(db, user)
    start = await client.get(
        f"/auth/app-login?rd={quote('https://annotation.iapportal.test/', safe='')}",
        headers={"cookie": cookie},
    )
    query = urlsplit(start.headers["location"]).query
    done = await check(authorization_client, "annotation.iapportal.test", f"/.iap-portal/callback?{query}")
    morsel = _cookie(done.headers["set-cookie"])["__Host-iap_app_session"]
    assert morsel["secure"] and morsel["domain"] == "" and morsel["path"] == "/"


async def test_revoke_and_disable_apis(client, db):
    user = await make_user(db, "bob@example.com")
    cookie, _ = await portal_session(db, user)
    admin = await make_user(db, "admin@example.com")
    admin_cookie, _ = await portal_session(db, admin)
    headers = {"cookie": admin_cookie, "origin": PORTAL}

    resp = await client.post("/api/users/bob@example.com/revoke-sessions", headers=headers)
    assert resp.status_code == 200
    assert (await client.get("/api/me", headers={"cookie": cookie})).status_code == 401

    cookie, _ = await portal_session(db, user)
    assert (await client.post("/api/users/bob@example.com/disable", headers=headers)).json()["disabled"]
    assert (await client.get("/api/me", headers={"cookie": cookie})).status_code == 401
    assert not (await client.post("/api/users/bob@example.com/enable", headers=headers)).json()["disabled"]
    assert (await client.get("/api/me", headers={"cookie": cookie})).status_code == 401
    cookie, _ = await portal_session(db, user)

    # Sessions revoked by the disable stay revoked after re-enabling.
    assert (await client.get("/api/me", headers={"cookie": cookie})).status_code == 200
    non_admin = {"cookie": cookie, "origin": PORTAL}
    assert (await client.post("/api/users/admin@example.com/disable", headers=non_admin)).status_code == 403


async def test_admin_demotion_takes_effect_on_next_request(client, db, settings):
    admin = await make_user(db, "admin@example.com")
    cookie, _ = await portal_session(db, admin)
    assert (await client.get("/api/apps", headers={"cookie": cookie})).status_code == 200
    assert (await client.get("/api/me", headers={"cookie": cookie})).json()["is_admin"] is True
    settings.set(admin_emails="")
    assert (await client.get("/api/apps", headers={"cookie": cookie})).status_code == 403
    assert (await client.get("/api/me", headers={"cookie": cookie})).json()["is_admin"] is False
