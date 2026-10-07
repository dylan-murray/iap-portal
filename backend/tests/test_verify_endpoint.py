"""ext_authz (/auth/verify) trust boundary.

The gateway sends the original Host and path. Only those and the app session
cookie decide the outcome; client-controlled forwarding headers are ignored.
"""

from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlsplit

import jwt as pyjwt
import pytest
from sqlalchemy import delete, func, select

from iap_portal_server.auth.jwks import current_jwks
from iap_portal_server.auth.session import revoke_session_tree, utcnow
from iap_portal_server.db.models import App, AppAccess, AuditEvent, AuthSession
from tests.conftest import PORTAL, app_session, check, make_app, make_user, portal_session

APP_HOST = "annotation.iapportal.test:8080"


async def _signed_in(db, email="owner@example.com", slug="annotation", owner=True, name="Owner"):
    user = await make_user(db, email, name=name)
    await make_app(db, slug, owner=user if owner else None)
    _, parent = await portal_session(db, user)
    return user, parent, await app_session(db, parent, slug)


# --- Authority -------------------------------------------------------------


async def test_x_forwarded_host_cannot_select_the_portal_branch(client, db, authorization_client):
    """Pre-fix: XFH=portal.* returned 200 for any app without a session."""
    await make_app(db, "annotation")
    for spoof in ("portal.iapportal.test", "portal.iapportal.test:8080", "nodot", ""):
        resp = await check(authorization_client, APP_HOST, "/label/1", **{"x-forwarded-host": spoof})
        assert resp.status_code == 302, spoof
        assert resp.headers["location"].startswith(f"{PORTAL}/auth/app-login?rd=")


async def test_portal_host_is_never_authorized(client, db, authorization_client):
    for host in ("portal.iapportal.test", "portal.iapportal.test:8080", "PORTAL.iapportal.test"):
        assert (await check(authorization_client, host)).status_code == 403


@pytest.mark.parametrize(
    "host",
    ["", "a..iapportal.test", "annotation.iapportal.test.", "annotation.iapportal.test:x",
     "annotation.iapportal.test:99999", "[::1]:8080", "user@annotation.iapportal.test",
     "anno tation.iapportal.test", "-bad.iapportal.test"],
)
async def test_malformed_hosts_are_rejected(client, host, authorization_client):
    assert (await check(authorization_client, host)).status_code == 400


@pytest.mark.parametrize(
    "host",
    ["evil.example.com", "iapportal.test", "iapportal.test.evil.com",
     "a.b.iapportal.test", "annotation.iapportal.test.evil.com", "1abc.iapportal.test"],
)
async def test_non_app_hosts_are_denied(client, host, authorization_client):
    assert (await check(authorization_client, host)).status_code == 403


async def test_host_matching_is_case_and_port_insensitive(client, db, authorization_client):
    _, _, cookie = await _signed_in(db)
    for host in ("ANNOTATION.iapportal.test", "annotation.iapportal.test:8080", "annotation.iapportal.test"):
        assert (await check(authorization_client, host, cookie=cookie)).status_code == 200, host


async def test_redirect_preserves_deep_link_without_trusting_forwarded_proto(client, db, authorization_client):
    await make_app(db, "annotation")
    resp = await check(authorization_client, APP_HOST, "/label/42?tab=a%26b&x=1", **{"x-forwarded-proto": "https"})
    location = urlsplit(resp.headers["location"])
    rd = parse_qs(location.query)["rd"][0]
    assert rd == "http://annotation.iapportal.test:8080/label/42?tab=a%26b&x=1"


async def test_unauthenticated_response_does_not_reveal_app_existence(client, db, authorization_client):
    await make_app(db, "annotation")
    known = await check(authorization_client, APP_HOST)
    unknown = await check(authorization_client, "ghost.iapportal.test")
    assert known.status_code == unknown.status_code == 302


# --- Sessions ---------------------------------------------------------------


async def test_owner_gets_identity_headers_and_app_scoped_jwt(client, db, authorization_client):
    user, _, cookie = await _signed_in(db)
    resp = await check(authorization_client, APP_HOST, cookie=cookie)
    assert resp.status_code == 200
    assert resp.headers["x-forwarded-email"] == "owner@example.com"
    assert resp.headers["x-forwarded-user"] == str(user.id)
    assert resp.headers["x-iap-portal-app"] == "annotation"
    token = resp.headers["authorization"].removeprefix("Bearer ")
    key = pyjwt.PyJWKSet.from_dict(current_jwks())["test-kid"].key
    claims = pyjwt.decode(token, key, algorithms=["RS256"], audience="annotation", issuer=PORTAL)
    assert claims["sub"] == str(user.id) and claims["email"] == "owner@example.com"
    with pytest.raises(pyjwt.InvalidAudienceError):
        pyjwt.decode(token, key, algorithms=["RS256"], audience="other-app")


async def test_portal_session_cookie_is_not_accepted_on_app_hosts(client, db, authorization_client):
    """A portal cookie (e.g. a legacy parent-domain cookie or one tossed by a
    sibling) must not authenticate app requests."""
    user = await make_user(db, "owner@example.com")
    await make_app(db, "annotation", owner=user)
    portal_cookie, _ = await portal_session(db, user)
    resp = await check(authorization_client, APP_HOST, cookie=portal_cookie)
    assert resp.status_code == 302


async def test_app_session_is_bound_to_its_app(client, db, authorization_client):
    user, parent, cookie_a = await _signed_in(db)
    await make_app(db, "other", owner=user)
    assert (await check(authorization_client, "other.iapportal.test", cookie=cookie_a)).status_code == 302


async def test_revoked_parent_session_invalidates_app_session(client, db, authorization_client):
    _, parent, cookie = await _signed_in(db)
    assert (await check(authorization_client, APP_HOST, cookie=cookie)).status_code == 200
    await revoke_session_tree(db, parent.id)
    await db.commit()
    assert (await check(authorization_client, APP_HOST, cookie=cookie)).status_code == 302


async def test_expired_and_disabled_sessions_are_rejected(client, db, authorization_client):
    user, parent, cookie = await _signed_in(db)
    user.disabled_at = utcnow()
    await db.commit()
    assert (await check(authorization_client, APP_HOST, cookie=cookie)).status_code == 302
    user.disabled_at = None
    await db.commit()
    assert (await check(authorization_client, APP_HOST, cookie=cookie)).status_code == 200
    (await db.execute(select(AuthSession).where(AuthSession.id == parent.id))).scalar_one().expires_at = utcnow()
    await db.commit()
    assert (await check(authorization_client, APP_HOST, cookie=cookie)).status_code == 302


# --- Authorization ----------------------------------------------------------


async def test_denial_is_audited_once_per_window(client, db, authorization_client):
    _, _, cookie = await _signed_in(db, email="outsider@example.com", owner=False)
    for _ in range(5):
        assert (await check(authorization_client, APP_HOST, cookie=cookie)).status_code == 403
    count = (
        await db.execute(select(func.count()).select_from(AuditEvent).where(AuditEvent.event_type == "access_denied"))
    ).scalar_one()
    assert count == 1


async def test_grants_are_checked_live(client, db, authorization_client):
    user, _, cookie = await _signed_in(db, email="bob@example.com", owner=False)
    assert (await check(authorization_client, APP_HOST, cookie=cookie)).status_code == 403
    app = (await db.execute(select(App))).scalar_one()
    db.add(AppAccess(app_id=app.id, user_id=user.id, granted_by_user_id=None))
    await db.commit()
    assert (await check(authorization_client, APP_HOST, cookie=cookie)).status_code == 200
    await db.execute(delete(AppAccess))
    await db.commit()
    assert (await check(authorization_client, APP_HOST, cookie=cookie)).status_code == 403


async def test_admin_demotion_applies_to_existing_sessions(client, db, settings, authorization_client):
    user, _, cookie = await _signed_in(db, email="admin@example.com", owner=False)
    assert (await check(authorization_client, APP_HOST, cookie=cookie)).status_code == 200
    settings.set(admin_emails="someone-else@example.com")
    assert (await check(authorization_client, APP_HOST, cookie=cookie)).status_code == 403


async def test_disabled_app_is_not_found(client, db, authorization_client):
    user, parent, _ = await _signed_in(db)
    await make_app(db, "off", enabled=False, owner=user)
    cookie = await app_session(db, parent, "off")
    assert (await check(authorization_client, "off.iapportal.test", cookie=cookie)).status_code == 404


# --- Upstream request hygiene ------------------------------------------------


async def test_portal_cookies_are_stripped_from_upstream(client, db, authorization_client):
    _, _, cookie = await _signed_in(db)
    mixed = f"theme=dark; {cookie}; iap_portal_session=legacy; __Host-iap_portal_session=x; _xsrf=1"
    resp = await check(authorization_client, APP_HOST, cookie=mixed)
    assert resp.status_code == 200
    assert resp.headers["cookie"] == "theme=dark; _xsrf=1"

    only_ours = await check(authorization_client, APP_HOST, cookie=cookie)
    assert "cookie" not in only_ours.headers
    assert "cookie" in only_ours.headers["x-envoy-auth-headers-to-remove"].split(",")


async def test_empty_identity_values_are_removed_not_left_to_the_client(client, db, authorization_client):
    _, _, cookie = await _signed_in(db, name=None)
    resp = await check(authorization_client, APP_HOST, cookie=cookie, **{"x-forwarded-name": "Mallory", "x-forwarded-groups": "admins"})
    removed = resp.headers["x-envoy-auth-headers-to-remove"].split(",")
    assert {"x-forwarded-name", "x-forwarded-groups"} <= set(removed)
    assert "x-forwarded-name" not in resp.headers


# --- Cross-origin requests into apps -------------------------------------------


@pytest.mark.parametrize(
    "origin,status",
    [
        (None, 200),
        ("http://annotation.iapportal.test:8080", 200),
        ("http://evil.iapportal.test:8080", 403),
        ("https://annotation.iapportal.test", 403),
        (PORTAL, 403),
        ("null", 403),
    ],
)
async def test_cross_origin_requests_to_apps_are_denied(client, db, origin, status, authorization_client):
    _, _, cookie = await _signed_in(db)
    headers = {"cookie": cookie}
    if origin:
        headers["origin"] = origin
    resp = await check(authorization_client, APP_HOST, "/api/delete", method="POST", **headers)
    assert resp.status_code == status
    ws = await check(authorization_client, APP_HOST, "/_stcore/stream", upgrade="websocket", **headers)
    assert ws.status_code == status


async def test_verify_decodes_nothing_from_client_authorization(client, db, authorization_client):
    await make_app(db, "annotation")
    resp = await check(authorization_client, APP_HOST, authorization="Bearer forged.jwt.value")
    assert resp.status_code == 302


async def test_callback_path_is_never_forwarded(client, db, authorization_client):
    await make_app(db, "annotation")
    resp = await check(authorization_client, APP_HOST, "/.iap-portal/callback?code=bogus")
    assert resp.status_code == 302
    assert unquote(resp.headers["location"]).endswith("rd=http://annotation.iapportal.test:8080/")
