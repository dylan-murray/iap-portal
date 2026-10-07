"""OIDC sign-in: verified claims, admission, and account-linking safety."""

from __future__ import annotations

import json

import httpx
import jwt as pyjwt
import pytest
from sqlalchemy import func, select

from iap_portal_server.api import login as login_api
from iap_portal_server.auth import k8s
from iap_portal_server.auth.identity import LoginRejected, resolve_user, verified_claims
from iap_portal_server.auth.session import portal_cookie_name, utcnow
from iap_portal_server.config import get_settings
from iap_portal_server.db.models import IdentityProvider, User, UserIdentity
from tests.conftest import OPERATOR_TOKEN, PORTAL, make_user

OKTA = "https://tenant.okta.example.com/oauth2/default"
GOOGLE = "https://accounts.google.com"


def _claims(sub="u1", email="alice@example.com", verified=True, **extra):
    claims = {"iss": OKTA, "sub": sub, "email": email, "name": "Alice", **extra}
    if verified is not None:
        claims["email_verified"] = verified
    return claims


async def _login(db, provider="okta", issuer=OKTA, **kw):
    settings = get_settings()
    claims = verified_claims(provider, issuer, _claims(**kw), settings)
    user, how = await resolve_user(db, claims, settings)
    await db.commit()
    return user, how


# --- Claim requirements and admission ------------------------------------------


@pytest.mark.parametrize("verified", [None, False, "false", "yes"])
def test_unverified_or_missing_email_verified_is_rejected(verified):
    with pytest.raises(LoginRejected) as err:
        verified_claims("okta", OKTA, _claims(verified=verified), get_settings())
    assert err.value.reason == "email_unverified"


def test_missing_subject_or_email_is_rejected():
    for claims in ({"email": "a@example.com", "email_verified": True}, {"sub": "u1", "email_verified": True}):
        with pytest.raises(LoginRejected):
            verified_claims("okta", OKTA, claims, get_settings())


def test_admission_by_email_domain_and_google_hosted_domain(settings):
    settings.set(allowed_email_domains=["example.com"])
    verified_claims("okta", OKTA, _claims(), get_settings())
    with pytest.raises(LoginRejected):
        verified_claims("okta", OKTA, _claims(email="x@partner.example.org"), get_settings())

    settings.set(allowed_email_domains=[], google_hosted_domains=["example.com"])
    verified_claims("google", GOOGLE, _claims(hd="example.com"), get_settings())
    with pytest.raises(LoginRejected) as err:
        verified_claims("google", GOOGLE, _claims(), get_settings())  # consumer account, no hd
    assert err.value.reason == "hd_not_allowed"


# --- Linking ---------------------------------------------------------------------


async def test_same_issuer_and_subject_is_the_same_user_even_after_email_change(db):
    first, how = await _login(db)
    assert how == "created"
    again, how = await _login(db, email="alice.renamed@example.com")
    assert how == "existing" and again.id == first.id and again.email == "alice.renamed@example.com"


async def test_recycled_email_with_new_subject_is_not_linked(db):
    """Same issuer, different subject: e.g. a reassigned mailbox. Never auto-link."""
    await _login(db, sub="old-employee")
    with pytest.raises(LoginRejected) as err:
        await _login(db, sub="new-employee")
    assert err.value.reason == "identity_mismatch"


async def test_placeholder_accounts_are_claimed_by_verified_email(db):
    placeholder = await make_user(db, "alice@example.com")  # e.g. listed as an owner
    user, how = await _login(db)
    assert how == "claimed" and user.id == placeholder.id


async def test_cross_issuer_linking_is_opt_in(db, settings):
    await _login(db)
    with pytest.raises(LoginRejected):
        await _login(db, provider="google", issuer=GOOGLE, sub="g-1")
    settings.set(link_verified_email_across_issuers=True)
    user, how = await _login(db, provider="google", issuer=GOOGLE, sub="g-1")
    assert how == "linked"
    count = (await db.execute(select(func.count()).select_from(UserIdentity).where(UserIdentity.user_id == user.id))).scalar_one()
    assert count == 2


async def test_email_change_cannot_take_over_another_account(db):
    await _login(db, sub="u1", email="alice@example.com")
    await _login(db, sub="u2", email="bob@example.com")
    with pytest.raises(LoginRejected) as err:
        await _login(db, sub="u2", email="alice@example.com")
    assert err.value.reason == "email_conflict"


async def test_legacy_identity_without_issuer_is_upgraded(db):
    user = await make_user(db, "alice@example.com")
    db.add(UserIdentity(user_id=user.id, provider=IdentityProvider.OKTA, issuer=None, subject="u1", raw_claims={}))
    await db.commit()
    again, how = await _login(db)
    assert how == "existing" and again.id == user.id
    identity = (await db.execute(select(UserIdentity))).scalar_one()
    assert identity.issuer == OKTA


async def test_admin_unlink_allows_deliberate_relink(client, db):
    await _login(db, sub="old-employee")
    resp = await client.delete(
        "/api/users/alice@example.com/identities", headers={"authorization": f"Bearer {OPERATOR_TOKEN}"}
    )
    assert resp.status_code == 200 and resp.json()["identities"] == 0
    user, how = await _login(db, sub="new-employee")
    assert how == "claimed"


# --- Callback: no unverified ID token decoding ----------------------------------


class _FakeClient:
    def __init__(self, token):
        self._token = token

    async def authorize_access_token(self, request):
        return self._token

    async def load_server_metadata(self):
        return {"issuer": GOOGLE}


def _unsigned_id_token(claims):
    return pyjwt.encode(claims, key="", algorithm="none")


@pytest.fixture
def fake_google(monkeypatch):
    holder = {}

    class _OAuth:
        @property
        def google(self):
            return _FakeClient(holder["token"])

    monkeypatch.setattr(login_api, "oauth", lambda: _OAuth())
    return holder


async def test_callback_never_decodes_unverified_id_token(client, db, fake_google):
    forged = {"iss": GOOGLE, "sub": "x", "email": "admin@example.com", "email_verified": True}
    fake_google["token"] = {"id_token": _unsigned_id_token(forged)}  # no validated userinfo
    resp = await client.get("/auth/callback/google")
    assert resp.status_code == 400
    assert portal_cookie_name() not in resp.headers.get("set-cookie", "")
    assert (await db.execute(select(func.count()).select_from(User))).scalar_one() == 0


async def test_callback_rejects_issuer_mismatch(client, fake_google):
    fake_google["token"] = {"userinfo": {**_claims(), "iss": "https://evil.example.com"}}
    assert (await client.get("/auth/callback/google")).status_code == 400


async def test_callback_signs_in_with_validated_claims(client, db, fake_google):
    fake_google["token"] = {"userinfo": {**_claims(sub="g-7"), "iss": GOOGLE}}
    resp = await client.get("/auth/callback/google")
    assert resp.status_code == 302 and resp.headers["location"] == PORTAL + "/"
    assert portal_cookie_name() in resp.headers["set-cookie"]
    identity = (await db.execute(select(UserIdentity))).scalar_one()
    assert (identity.issuer, identity.subject) == (GOOGLE, "g-7")


async def test_callback_rejects_disabled_users(client, db, fake_google):
    fake_google["token"] = {"userinfo": {**_claims(sub="g-7"), "iss": GOOGLE}}
    await client.get("/auth/callback/google")
    user = (await db.execute(select(User))).scalar_one()
    user.disabled_at = utcnow()
    await db.commit()
    resp = await client.get("/auth/callback/google")
    assert resp.status_code == 403


# --- TokenReview client ------------------------------------------------------------


async def test_token_review_requires_authenticated_and_audience(monkeypatch, tmp_path):
    (tmp_path / "token").write_text("portal-sa-token")
    (tmp_path / "ca.crt").write_text("")
    monkeypatch.setattr(k8s, "SERVICE_ACCOUNT_DIR", tmp_path)
    responses = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer portal-sa-token"
        assert body["spec"]["audiences"] == ["iap-portal"]
        return httpx.Response(200, json={"status": responses.pop(0)})

    real = httpx.AsyncClient
    monkeypatch.setattr(k8s.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))
    user = {"username": "system:serviceaccount:iap-app-a:iap-portal-registration"}
    responses.extend([
        {"authenticated": True, "audiences": ["iap-portal"], "user": user},
        {"authenticated": True, "audiences": ["kubernetes"], "user": user},
        {"authenticated": False},
    ])
    assert await k8s.review_token("a.b.c") == user["username"]
    assert await k8s.review_token("a.b.c") is None
    assert await k8s.review_token("a.b.c") is None
