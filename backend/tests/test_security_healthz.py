"""/healthz/security and startup configuration checks."""

from __future__ import annotations

import os

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from iap_portal_server.config import Settings, get_settings
from iap_portal_server.security import check_settings

GOOD_SECRET = "Qm9vdHN0cmFwLXNlY3JldC12YWx1ZS1mb3ItdGVzdHM="


def _pem_pair(bits=2048):
    key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return private, public


def _production(**overrides) -> Settings:
    s = get_settings()
    values = dict(
        env="production",
        portal_base_url="https://portal.apps.example.org",
        session_secret=GOOD_SECRET,
        admin_api_token="",
        okta_issuer="https://tenant.okta.example.org",
        google_client_id="",
        enable_dev_login=False,
        jwt_private_key_pem=s.jwt_private_key_pem,
        jwt_public_key_pem=s.jwt_public_key_pem,
    )
    values.update(overrides)
    return Settings(**values)


async def test_healthz_security_reports_ok_without_details(client):
    resp = await client.get("/healthz/security")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_healthz_security_fails_on_unsafe_config(client, settings):
    settings.set(session_secret="short")
    resp = await client.get("/healthz/security")
    assert resp.status_code == 500 and resp.json() == {"status": "fail"}


async def test_security_health_accepts_mounted_signing_keys(client, settings, tmp_path):
    private = tmp_path / "private.pem"
    public = tmp_path / "public.pem"
    private.write_text(settings.jwt_private_key_pem)
    public.write_text(settings.jwt_public_key_pem)
    settings.set(
        jwt_private_key_pem="", jwt_public_key_pem="",
        jwt_private_key_pem_file=str(private), jwt_public_key_pem_file=str(public),
    )
    assert (await client.get("/healthz/security")).status_code == 200


def test_default_environment_is_production(monkeypatch):
    monkeypatch.delenv("PORTAL_ENV")
    assert Settings().env == "production"
    assert Settings.model_config.get("env_file") is None  # no implicit .env loading


def test_valid_production_config_passes():
    assert check_settings(_production()).fatal == []


@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"portal_base_url": "http://portal.apps.example.org"}, "https"),
        ({"session_secret": "dev-secret-change-me-to-something-32chars"}, "sample"),
        ({"session_secret": "too-short"}, "at least 32"),
        ({"admin_api_token": "dev-admin-token-change-me-in-prod"}, "PORTAL_ADMIN_API_TOKEN"),
        ({"admin_api_token": "short"}, "PORTAL_ADMIN_API_TOKEN"),
        ({"okta_issuer": ""}, "identity provider"),
        ({"enable_dev_login": True}, "PORTAL_ENABLE_DEV_LOGIN"),
        ({"jwt_public_key_pem": _pem_pair()[1]}, "does not match"),
        ({"jwt_private_key_pem": "", "jwt_private_key_pem_file": ""}, "JWT keys"),
        ({"portal_base_url": "https://portal.apps.example.org/prefix"}, "origin"),
        ({"portal_base_url": "https://user@portal.apps.example.org"}, "origin"),
        ({"session_ttl_seconds": 0}, "SESSION_TTL"),
    ],
)
def test_unsafe_production_config_is_fatal(overrides, fragment):
    fatal = check_settings(_production(**overrides)).fatal
    assert any(fragment in problem for problem in fatal), fatal


def test_small_signing_key_is_fatal():
    private, public = _pem_pair(1024)
    fatal = check_settings(_production(jwt_private_key_pem=private, jwt_public_key_pem=public)).fatal
    assert any("2048" in p for p in fatal)


def test_dev_mode_is_limited_to_local_hostnames():
    base = dict(session_secret=GOOD_SECRET, env="dev")
    assert check_settings(Settings(portal_base_url="http://localhost:5173", **base)).fatal == []
    assert check_settings(Settings(portal_base_url="http://portal.iapportal.test:8090", **base)).fatal == []
    fatal = check_settings(Settings(portal_base_url="https://portal.apps.example.org", **base)).fatal
    assert any("only allowed" in p for p in fatal)


def test_deprecated_settings_are_reported(monkeypatch):
    monkeypatch.setitem(os.environ, "PORTAL_COOKIE_DOMAIN", ".apps.example.org")
    warnings = check_settings(_production()).warnings
    assert any("PORTAL_COOKIE_DOMAIN" in w for w in warnings)


def test_open_google_admission_is_warned():
    warnings = check_settings(_production(google_client_id="gid")).warnings
    assert any("any verified Google account" in w for w in warnings)
    assert not any(
        "any verified Google account" in w
        for w in check_settings(_production(google_client_id="gid", google_hosted_domains=["example.org"])).warnings
    )


def test_api_docs_are_not_served_in_production(monkeypatch):
    from iap_portal_server.main import create_app

    monkeypatch.setattr("iap_portal_server.main.enforce_settings", lambda s: None)
    monkeypatch.setattr("iap_portal_server.main.get_settings", lambda: _production())
    app = create_app()
    assert app.openapi_url is None and app.docs_url is None and app.redoc_url is None
