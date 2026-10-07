"""CSRF (Origin) protection, scoped registration credentials, and rate limits."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from iap_portal_server.auth.k8s import TokenReviewUnavailable, registration_slug
from iap_portal_server.db.models import App, AppOwner, User
from iap_portal_server.ratelimit import client_ip
from tests.conftest import OPERATOR_TOKEN, PORTAL, check, make_app, make_user, portal_session

SA_TOKEN = "header.payload.signature"


def _app_body(slug: str, owners=()):
    return {
        "slug": slug,
        "display_name": slug,
        "upstream_service": f"{slug}.iap-app-{slug}.svc.cluster.local",
        "upstream_port": 8090,
        "owners": list(owners),
    }


# --- Origin / CSRF -------------------------------------------------------------


@pytest.mark.parametrize(
    "headers,allowed",
    [
        ({"origin": PORTAL}, True),
        ({"sec-fetch-site": "same-origin"}, True),
        ({"origin": "http://evil.iapportal.test:8080"}, False),  # sibling app, same site
        ({"origin": "https://evil.example.com"}, False),
        ({"origin": "null"}, False),
        ({}, False),  # no Origin, no Sec-Fetch-Site
        ({"sec-fetch-site": "same-site"}, False),
    ],
)
async def test_cookie_mutations_require_portal_origin(client, db, headers, allowed):
    owner = await make_user(db, "owner@example.com")
    await make_app(db, "annotation", owner=owner)
    cookie, _ = await portal_session(db, owner)
    resp = await client.post("/api/apps/annotation/disable", headers={"cookie": cookie, **headers})
    assert (resp.status_code == 200) is allowed, resp.text
    app = (await db.execute(select(App))).scalar_one()
    await db.refresh(app)
    assert app.is_enabled is (not allowed)


async def test_bearer_callers_are_not_subject_to_origin_checks(client, db):
    await make_app(db, "annotation")
    resp = await client.post(
        "/api/apps/annotation/disable", headers={"authorization": f"Bearer {OPERATOR_TOKEN}"}
    )
    assert resp.status_code == 200


async def test_gateway_checks_with_unsafe_methods_are_not_blocked(client, db, authorization_client):
    await make_app(db, "annotation")
    resp = await check(authorization_client, "annotation.iapportal.test", "/submit", method="POST")
    assert resp.status_code == 302  # sign-in redirect, not a CSRF rejection


async def test_security_headers_on_portal_responses(client):
    resp = await client.get("/healthz")
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["x-content-type-options"] == "nosniff"


# --- Registration credentials ----------------------------------------------------


@pytest.fixture
def token_review(monkeypatch, settings):
    settings.set(kubernetes_registration_enabled=True)
    identities = {}

    async def fake_review(token, settings=None):
        if token == "down.down.down":
            raise TokenReviewUnavailable("api unreachable")
        return identities.get(token)

    monkeypatch.setattr("iap_portal_server.auth.principals.review_token", fake_review)
    return identities


def _auth(token=SA_TOKEN):
    return {"authorization": f"Bearer {token}"}


async def test_registration_identity_is_scoped_to_its_own_app(client, db, token_review):
    token_review[SA_TOKEN] = "system:serviceaccount:iap-app-alpha:iap-portal-registration"
    victim_owner = await make_user(db, "victim@example.com")
    await make_app(db, "beta", owner=victim_owner)

    assert (await client.post("/api/apps", json=_app_body("alpha", ["a@example.com"]), headers=_auth())).status_code == 201
    updated = await client.put("/api/apps/alpha", json=_app_body("alpha", ["b@example.com"]), headers=_auth())
    assert updated.status_code == 200 and updated.json()["owners"] == ["b@example.com"]

    # Nothing about another app can be touched.
    assert (await client.post("/api/apps", json=_app_body("gamma"), headers=_auth())).status_code == 403
    takeover = _app_body("beta", ["attacker@example.com"])
    assert (await client.put("/api/apps/beta", json=takeover, headers=_auth())).status_code == 403
    assert (await client.put("/api/apps/alpha", json=takeover, headers=_auth())).status_code == 422
    assert (await client.post("/api/apps/beta/owners", json={"email": "x@example.com"}, headers=_auth())).status_code == 403
    assert (await client.post("/api/apps/beta/access", json={"user_email": "victim@example.com"}, headers=_auth())).status_code == 403
    assert (await client.post("/api/apps/beta/disable", headers=_auth())).status_code == 403
    assert (await client.get("/api/apps", headers=_auth())).status_code == 403
    assert (await client.post("/api/groups", json={"name": "x-team"}, headers=_auth())).status_code == 403
    # Its own app: no grants, no enable/disable (an admin may have disabled it).
    assert (await client.post("/api/apps/alpha/access", json={"user_email": "b@example.com"}, headers=_auth())).status_code == 403
    assert (await client.post("/api/apps/alpha/disable", headers=_auth())).status_code == 403

    beta_owners = (
        await db.execute(
            select(User.email).join(AppOwner, AppOwner.user_id == User.id).join(App).where(App.slug == "beta")
        )
    ).scalars().all()
    assert beta_owners == ["victim@example.com"]


@pytest.mark.parametrize(
    "username",
    [
        "system:serviceaccount:iap-app-alpha:default",
        "system:serviceaccount:team-alpha:iap-portal-registration",
        "system:serviceaccount:iap-app-portal:iap-portal-registration",
        "system:serviceaccount:iap-app-Bad_Slug:iap-portal-registration",
        "alice@example.com",
        None,
    ],
)
async def test_other_identities_cannot_register(client, token_review, username):
    token_review[SA_TOKEN] = username
    assert (await client.post("/api/apps", json=_app_body("alpha"), headers=_auth())).status_code == 401


def test_registration_slug_mapping(settings):
    ok = "system:serviceaccount:iap-app-alpha:iap-portal-registration"
    assert registration_slug(ok) == "alpha"
    settings.set(app_namespace_prefix="apps-")
    assert registration_slug(ok) is None


async def test_token_review_outage_fails_closed(client, token_review):
    resp = await client.post("/api/apps", json=_app_body("alpha"), headers=_auth("down.down.down"))
    assert resp.status_code == 503


async def test_workload_tokens_are_ignored_unless_enabled(client, monkeypatch):
    async def must_not_call(*args, **kwargs):
        raise AssertionError("TokenReview called while disabled")

    monkeypatch.setattr("iap_portal_server.auth.principals.review_token", must_not_call)
    assert (await client.post("/api/apps", json=_app_body("alpha"), headers=_auth())).status_code == 401


async def test_operator_token_is_admin_equivalent(client, db):
    resp = await client.post("/api/apps", json=_app_body("alpha", ["o@example.com"]), headers=_auth(OPERATOR_TOKEN))
    assert resp.status_code == 201
    grant = await client.post(
        "/api/apps/alpha/access", json={"user_email": "o@example.com"}, headers=_auth(OPERATOR_TOKEN)
    )
    assert grant.status_code == 201
    assert (await client.put("/api/apps/alpha", json=_app_body("alpha", ["p@example.com"]), headers=_auth(OPERATOR_TOKEN))).json()["owners"] == ["p@example.com"]


async def test_reserved_slugs_cannot_be_registered(client):
    resp = await client.post("/api/apps", json=_app_body("portal"), headers=_auth(OPERATOR_TOKEN))
    assert resp.status_code == 422


async def test_sessions_cannot_use_end_user_endpoints_with_bearer(client):
    assert (await client.get("/api/me", headers=_auth(OPERATOR_TOKEN))).status_code == 403


# --- Rate limits -----------------------------------------------------------------


async def test_failed_bearer_attempts_are_rate_limited(client, settings):
    settings.set(rate_limit_bearer_failures_per_minute=3)
    statuses = [
        (await client.get("/api/apps", headers={**_auth("wrong"), "x-forwarded-for": "203.0.113.9"})).status_code
        for _ in range(5)
    ]
    assert statuses == [401, 401, 401, 429, 429]
    # The operator token from another client address is unaffected.
    ok = await client.get("/api/apps", headers={**_auth(OPERATOR_TOKEN), "x-forwarded-for": "198.51.100.7"})
    assert ok.status_code == 200


async def test_sign_in_is_rate_limited_per_client(client, settings):
    settings.set(rate_limit_login_per_minute=2)
    codes = [
        (await client.get("/login/okta", headers={"x-forwarded-for": "203.0.113.5"})).status_code
        for _ in range(3)
    ]
    assert codes[-1] == 429


async def test_cookie_mutations_are_rate_limited_per_user(client, db, settings):
    settings.set(rate_limit_mutations_per_minute=2)
    owner = await make_user(db, "owner@example.com")
    await make_app(db, "annotation", owner=owner)
    cookie, _ = await portal_session(db, owner)
    headers = {"cookie": cookie, "origin": PORTAL}
    codes = [(await client.post("/api/apps/annotation/disable", headers=headers)).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


class _Req:
    def __init__(self, xff=None, peer="10.0.0.9"):
        self.headers = {"x-forwarded-for": xff} if xff else {}
        self.client = type("C", (), {"host": peer})()


def test_client_ip_uses_trusted_hop_count(settings):
    assert client_ip(_Req("198.51.100.1, 203.0.113.7")) == "203.0.113.7"  # spoofed left entry ignored
    settings.set(trusted_proxy_hops=2)
    assert client_ip(_Req("198.51.100.1, 203.0.113.7, 10.1.1.1")) == "203.0.113.7"
    assert client_ip(_Req("10.1.1.1")) == "10.0.0.9"  # fewer entries than trusted hops
    settings.set(trusted_proxy_hops=0)
    assert client_ip(_Req("198.51.100.1")) == "10.0.0.9"
