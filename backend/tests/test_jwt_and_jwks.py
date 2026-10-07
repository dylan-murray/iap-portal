"""JWT minting, JWKS publication, overlapping key rotation, key consistency."""

from __future__ import annotations

import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import PyJWKSet

from iap_portal_server.auth.jwks import current_jwks, key_problems, verification_keys
from iap_portal_server.auth.jwt import mint_for_app
from iap_portal_server.config import get_settings


def _mint(slug="annotation"):
    return mint_for_app(user_id=7, email="alice@example.com", name="Alice", groups=["engineering"], app_slug=slug)


def _public_pem(key):
    return key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()


def test_jwks_endpoint_shape_is_valid():
    jwks = current_jwks()
    assert len(jwks["keys"]) == 1
    k = jwks["keys"][0]
    assert (k["kty"], k["alg"], k["use"], k["kid"]) == ("RSA", "RS256", "sig", get_settings().jwt_kid)


def test_mint_and_roundtrip_verify_with_jwks():
    token = _mint()
    key = PyJWKSet.from_dict(current_jwks())[pyjwt.get_unverified_header(token)["kid"]].key
    claims = pyjwt.decode(token, key, algorithms=["RS256"], audience="annotation", issuer=get_settings().issuer)
    assert claims["sub"] == "7" and claims["email"] == "alice@example.com"
    assert claims["iss"] == "http://portal.iapportal.test:8080"
    assert claims["exp"] - claims["iat"] == get_settings().jwt_ttl_seconds
    assert claims["exp"] > time.time()


def test_wrong_audience_and_tampering_are_rejected():
    token = _mint()
    key = PyJWKSet.from_dict(current_jwks())["test-kid"].key
    with pytest.raises(pyjwt.InvalidAudienceError):
        pyjwt.decode(token, key, algorithms=["RS256"], audience="OTHER-APP")
    header, payload, sig = token.split(".")
    tampered = ".".join([header, ("A" if payload[0] != "A" else "B") + payload[1:], sig])
    with pytest.raises(pyjwt.InvalidTokenError):
        pyjwt.decode(tampered, key, algorithms=["RS256"], audience="annotation")


def test_additional_keys_are_published_for_rotation(settings, tmp_path):
    previous = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    (tmp_path / "portal-v0.pem").write_text(_public_pem(previous))
    settings.set(jwt_additional_public_keys_dir=str(tmp_path))
    kids = [k["kid"] for k in current_jwks()["keys"]]
    assert kids == ["test-kid", "portal-v0"]
    old_token = pyjwt.encode(
        {"sub": "1", "aud": "annotation", "exp": int(time.time()) + 60}, previous, algorithm="RS256",
        headers={"kid": "portal-v0"},
    )
    key = PyJWKSet.from_dict(current_jwks())["portal-v0"].key
    assert pyjwt.decode(old_token, key, algorithms=["RS256"], audience="annotation")["sub"] == "1"
    assert key_problems() == []


def test_duplicate_or_invalid_kids_are_configuration_errors(settings, tmp_path):
    (tmp_path / "test-kid.pem").write_text(settings.jwt_public_key_pem)
    settings.set(jwt_additional_public_keys_dir=str(tmp_path))
    assert any("duplicate kid" in p for p in key_problems())
    with pytest.raises(RuntimeError):
        verification_keys()


def test_mismatched_keypair_is_detected(settings):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings.set(jwt_public_key_pem=_public_pem(other))
    assert any("does not match" in p for p in key_problems())
