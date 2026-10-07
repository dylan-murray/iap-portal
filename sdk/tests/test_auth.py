"""SDK token verification, JWKS behavior, and framework adapters."""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib
import json
import sys
import time
import types

import httpx
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI, WebSocket
from fastapi.testclient import TestClient
from flask import Flask
from jwt.algorithms import RSAAlgorithm
from starlette.websockets import WebSocketDisconnect

from iap_portal.auth import core, jwks
from iap_portal.auth import fastapi as fastapi_auth
from iap_portal.auth.core import AuthError, InvalidToken, TokenExpired, current_user, user_from_request, verify_jwt
from iap_portal.auth.fastapi import protect
from iap_portal.auth.flask import register_auth, require_user as flask_require_user

ISSUER = "https://portal.apps.example.com"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks(*pairs):
    keys = []
    for kid, key in pairs:
        jwk = RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
        jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
        keys.append(jwk)
    return {"keys": keys}


class _StaticCache(jwks.JWKSCache):
    def __init__(self, document):
        super().__init__("http://unused")
        self._document = document

    def _fetch(self):
        self._keys = {k.key_id: k for k in pyjwt.PyJWKSet.from_dict(self._document).keys}
        self._fetched_at = time.monotonic()


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("IAP_PORTAL_APP_SLUG", "annotation")
    monkeypatch.setenv("IAP_PORTAL_ISSUER", ISSUER)
    monkeypatch.delenv("IAP_PORTAL_DEV", raising=False)
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    monkeypatch.setattr(jwks, "_default_cache", _StaticCache(_jwks(("k1", KEY))))


def _token(key=KEY, kid="k1", alg="RS256", **overrides):
    now = int(time.time())
    claims = {"iss": ISSUER, "sub": "7", "aud": "annotation", "email": "alice@example.com",
              "name": "Alice", "groups": ["engineering"], "iat": now, "exp": now + 300}
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return pyjwt.encode(claims, key, algorithm=alg, headers={"kid": kid})


def _b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


# --- verify_jwt -------------------------------------------------------------------


def test_valid_token():
    user = verify_jwt(_token())
    assert (user.email, user.user_id, user.groups) == ("alice@example.com", "7", ["engineering"])


def test_alg_none_is_rejected():
    forged = f"{_b64({'alg': 'none', 'kid': 'k1'})}.{_b64({'iss': ISSUER, 'aud': 'annotation', 'sub': '1', 'email': 'x@example.com', 'iat': 1, 'exp': 9999999999})}."
    with pytest.raises(InvalidToken):
        verify_jwt(forged)


def test_hs256_signed_with_public_key_is_rejected():
    public_pem = KEY.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    header = _b64({"alg": "HS256", "kid": "k1", "typ": "JWT"})
    payload = _b64({"iss": ISSUER, "aud": "annotation", "sub": "1", "email": "x@example.com",
                    "iat": int(time.time()), "exp": int(time.time()) + 60})
    sig = base64.urlsafe_b64encode(
        hmac.new(public_pem, f"{header}.{payload}".encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    with pytest.raises(InvalidToken):
        verify_jwt(f"{header}.{payload}.{sig}")


@pytest.mark.parametrize(
    "overrides",
    [{"aud": "other-app"}, {"iss": "https://evil.example.com"}, {"iss": None},
     {"email": None}, {"sub": None}, {"iat": None}],
)
def test_wrong_or_missing_claims_are_rejected(overrides):
    with pytest.raises(InvalidToken):
        verify_jwt(_token(**overrides))


def test_wrong_signing_key_and_unknown_kid_are_rejected():
    with pytest.raises(InvalidToken):
        verify_jwt(_token(key=OTHER_KEY))
    with pytest.raises(InvalidToken):
        verify_jwt(_token(kid="unknown"))


def test_expired_token_raises_token_expired():
    now = int(time.time())
    with pytest.raises(TokenExpired):
        verify_jwt(_token(iat=now - 600, exp=now - 300))


def test_connection_age_bounds_expired_handshake_tokens():
    now = int(time.time())
    handshake = _token(iat=now - 900, exp=now - 600)  # 15-minute-old WebSocket
    assert verify_jwt(handshake, connection_max_age=3600).email == "alice@example.com"
    with pytest.raises(TokenExpired):
        verify_jwt(handshake, connection_max_age=600)
    with pytest.raises(InvalidToken):  # signature still enforced
        verify_jwt(_token(key=OTHER_KEY, iat=now - 900, exp=now - 600), connection_max_age=3600)


@pytest.mark.parametrize("missing", ["IAP_PORTAL_APP_SLUG", "IAP_PORTAL_ISSUER"])
def test_verification_fails_closed_without_audience_or_issuer(monkeypatch, missing):
    monkeypatch.delenv(missing)
    with pytest.raises(InvalidToken, match=missing):
        verify_jwt(_token())


def test_dev_identity_is_refused_inside_kubernetes(monkeypatch):
    monkeypatch.setenv("IAP_PORTAL_DEV", "1")
    assert user_from_request({}).email == "you@example.com"
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
    with pytest.raises(AuthError):
        user_from_request({})


# --- JWKS cache ------------------------------------------------------------------


def _serving(monkeypatch, documents):
    calls = []

    def handler(request):
        calls.append(request.url)
        doc = documents[min(len(calls), len(documents)) - 1]
        return httpx.Response(503) if doc is None else httpx.Response(200, json=doc)

    real = httpx.Client
    monkeypatch.setattr(jwks.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler)))
    return calls


def test_unknown_kids_cannot_amplify_jwks_fetches(monkeypatch):
    calls = _serving(monkeypatch, [_jwks(("k1", KEY))])
    cache = jwks.JWKSCache("http://portal/jwks", min_refresh=30)
    for i in range(50):
        with pytest.raises(KeyError):
            cache.get_key(f"random-{i}")
    assert len(calls) == 1


def test_rotation_picks_up_new_kid_after_refresh_interval(monkeypatch):
    calls = _serving(monkeypatch, [_jwks(("k1", KEY)), _jwks(("k1", KEY), ("k2", OTHER_KEY))])
    cache = jwks.JWKSCache("http://portal/jwks", min_refresh=0)
    cache.get_key("k1")
    assert cache.get_key("k2").key_id == "k2"
    assert len(calls) == 2


def test_stale_keys_survive_a_portal_outage_but_not_forever(monkeypatch):
    _serving(monkeypatch, [_jwks(("k1", KEY)), None])
    cache = jwks.JWKSCache("http://portal/jwks", ttl=0, min_refresh=0, max_stale=3600)
    cache.get_key("k1")
    assert cache.get_key("k1").key_id == "k1"  # refresh failed, stale key used
    cache._fetched_at -= 7200
    with pytest.raises(jwks.JWKSUnavailable):
        cache.get_key("k1")


def test_non_rsa_or_non_signing_keys_are_ignored(monkeypatch):
    doc = _jwks(("k1", KEY))
    doc["keys"][0]["use"] = "enc"
    _serving(monkeypatch, [doc])
    with pytest.raises(KeyError):
        jwks.JWKSCache("http://portal/jwks").get_key("k1")


# --- FastAPI --------------------------------------------------------------------------


def _fastapi_app():
    app = FastAPI()
    protect(app, public_paths=["/healthz"])

    @app.get("/")
    def index():
        return {"email": current_user().email}

    @app.get("/healthz")
    def health():
        return {"ok": True}

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        await socket.send_json({"email": current_user().email})
        await socket.close()

    return TestClient(app)


def test_fastapi_maps_bad_tokens_to_401_not_500():
    client = _fastapi_app()
    now = int(time.time())
    assert client.get("/", headers={"authorization": f"Bearer {_token()}"}).json() == {"email": "alice@example.com"}
    for token in (_token(aud="other"), _token(iat=now - 900, exp=now - 600), "garbage"):
        resp = client.get("/", headers={"authorization": f"Bearer {token}"})
        assert resp.status_code == 401
    assert client.get("/").status_code == 401
    assert client.get("/healthz").status_code == 200


def test_fastapi_protects_websockets():
    client = _fastapi_app()
    with client.websocket_connect("/ws", headers={"authorization": f"Bearer {_token()}"}) as socket:
        assert socket.receive_json() == {"email": "alice@example.com"}
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={"authorization": f"Bearer {_token(aud='x')}"}):
            pass


# --- Flask ------------------------------------------------------------------------------


def test_flask_maps_bad_tokens_to_401():
    app = Flask(__name__)
    register_auth(app)

    @app.route("/")
    @flask_require_user
    def index(user):
        return {"email": user.email}

    client = app.test_client()
    assert client.get("/", headers={"authorization": f"Bearer {_token()}"}).json == {"email": "alice@example.com"}
    assert client.get("/", headers={"authorization": f"Bearer {_token(iss='https://evil.example.com')}"}).status_code == 401
    assert client.get("/").status_code == 401


# --- Streamlit ------------------------------------------------------------------------


class _Stop(Exception):
    pass


@pytest.fixture
def fake_streamlit(monkeypatch):
    rendered = []
    st = types.ModuleType("streamlit")
    st.context = types.SimpleNamespace(headers={})
    st.markdown = lambda body, unsafe_allow_html=False: rendered.append(body)

    def stop():
        raise _Stop()

    st.stop = stop
    monkeypatch.setitem(sys.modules, "streamlit", st)
    sys.modules.pop("iap_portal.auth.streamlit", None)
    helper = importlib.import_module("iap_portal.auth.streamlit")  # binds to the fake module

    yield st, rendered, helper
    sys.modules.pop("iap_portal.auth.streamlit", None)


def test_streamlit_reruns_survive_token_expiry_within_connection_age(fake_streamlit, monkeypatch):
    st, rendered, helper = fake_streamlit
    now = int(time.time())
    st.context.headers = {"authorization": f"Bearer {_token(iat=now - 900, exp=now - 600)}"}
    assert helper.require_user().email == "alice@example.com"

    monkeypatch.setenv("IAP_PORTAL_MAX_CONNECTION_AGE_SECONDS", "600")
    with pytest.raises(_Stop):
        helper.require_user()
    assert "Reload to continue" in rendered[-1]

    st.context.headers = {"authorization": f"Bearer {_token(aud='other')}"}
    with pytest.raises(_Stop):
        helper.require_user()
    assert "Sign-in required" in rendered[-1]
    assert core.max_connection_age() == 600


@pytest.mark.parametrize("middleware", [True, False])
@pytest.mark.parametrize("accept", ["application/json", "text/html"])
def test_fastapi_auth_errors_do_not_expose_internal_details(monkeypatch, middleware, accept):
    def failed_auth(headers):
        raise AuthError("private-key-path/internal-jwks-error")

    monkeypatch.setattr(fastapi_auth, "user_from_request", failed_auth)
    app = FastAPI()
    if middleware:
        protect(app)
    else:
        fastapi_auth.install_unauthorized_handler(app)

    @app.get("/", dependencies=[] if middleware else [Depends(fastapi_auth.require_user)])
    def index():
        return {"ok": True}

    response = TestClient(app).get("/", headers={"accept": accept})
    assert response.status_code == 401
    assert "private-key-path" not in response.text
    assert "internal-jwks-error" not in response.text
    assert "unauthenticated" in response.text
