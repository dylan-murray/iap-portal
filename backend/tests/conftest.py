"""Test config: in-memory SQLite + RSA keypair + FastAPI TestClient."""

from __future__ import annotations

import os

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# Env MUST be set before iap_portal_server is imported anywhere — config is
# resolved at import time via pydantic_settings.
_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_priv_pem = _key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()
_pub_pem = _key.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()

os.environ.update(
    {
        "PORTAL_ENV": "test",
        "PORTAL_DATABASE_URL": "sqlite+aiosqlite:///:memory:",
        "PORTAL_SESSION_SECRET": "test-session-secret-must-be-long-enough-32c",
        "PORTAL_PORTAL_BASE_URL": "http://portal.iapportal.test:8080",
        "PORTAL_JWT_PRIVATE_KEY_PEM": _priv_pem,
        "PORTAL_JWT_PUBLIC_KEY_PEM": _pub_pem,
        "PORTAL_JWT_KID": "test-kid",
        "PORTAL_OKTA_ISSUER": "",
        "PORTAL_GOOGLE_CLIENT_ID": "test-google-client",
        "PORTAL_ADMIN_EMAILS": "admin@example.com",
        "PORTAL_ADMIN_API_TOKEN": "operator-token-0123456789abcdef0123456789",
    }
)

# Now it's safe to import the app.
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from iap_portal_server.auth import verify as verify_mod  # noqa: E402
from iap_portal_server.auth.session import (  # noqa: E402
    app_cookie_name,
    create_app_session,
    create_portal_session,
    portal_cookie_name,
)
from iap_portal_server.config import get_settings  # noqa: E402
from iap_portal_server.main import create_authorization_app  # noqa: E402
from iap_portal_server.db import session as session_mod  # noqa: E402
from iap_portal_server.db.models import App, AppOwner, Base, User  # noqa: E402

PORTAL = "http://portal.iapportal.test:8080"
OPERATOR_TOKEN = os.environ["PORTAL_ADMIN_API_TOKEN"]


@pytest.fixture(autouse=True)
async def _fresh_db(monkeypatch):
    """Give each test its own in-memory SQLite so they're independent."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    monkeypatch.setattr(session_mod, "_engine", engine)
    monkeypatch.setattr(session_mod, "_Session", Session)
    verify_mod._recent_denials.clear()

    yield Session

    await engine.dispose()


@pytest.fixture
def settings(monkeypatch):
    """The cached Settings; attribute changes are undone after the test."""
    s = get_settings()

    class _Patcher:
        def __getattr__(self, name):
            return getattr(s, name)

        def set(self, **values):
            for key, value in values.items():
                monkeypatch.setattr(s, key, value)

    return _Patcher()


@pytest.fixture
async def db(_fresh_db):
    async with _fresh_db() as s:
        yield s


@pytest.fixture
async def client(_fresh_db):
    """TestClient bound to the in-memory DB via dependency override."""
    from iap_portal_server.db.session import get_db as real_get_db
    from iap_portal_server.main import create_app

    app = create_app()

    async def _override():
        async with _fresh_db() as s:
            yield s

    app.dependency_overrides[real_get_db] = _override

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url=PORTAL) as c:
        yield c


async def make_user(db, email: str, *, name: str | None = None) -> User:
    user = User(email=email, name=name, is_admin=False)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def make_app(db, slug: str, *, enabled: bool = True, owner: User | None = None) -> App:
    app = App(
        slug=slug,
        display_name=slug,
        upstream_service=f"{slug}.svc",
        upstream_port=8080,
        health_check_path="/healthz",
        is_enabled=enabled,
    )
    db.add(app)
    await db.flush()
    if owner is not None:
        db.add(AppOwner(app_id=app.id, user_id=owner.id))
    await db.commit()
    await db.refresh(app)
    return app


async def portal_session(db, user: User):
    """(cookie header, session row) for a portal session."""
    token, row = await create_portal_session(db, user, "okta")
    await db.commit()
    return f"{portal_cookie_name()}={token}", row


async def app_session(db, parent, slug: str) -> str:
    token, _ = await create_app_session(db, parent, slug)
    await db.commit()
    return f"{app_cookie_name()}={token}"


async def check(client, host: str, path: str = "/", method: str = "GET", **headers):
    """Call /auth/verify the way the gateway does: original Host and path."""
    return await client.request(method, "/auth/verify" + path, headers={"host": host, **headers})


@pytest.fixture
async def authorization_client(_fresh_db):
    transport = ASGITransport(app=create_authorization_app())
    async with AsyncClient(transport=transport, base_url="http://authorization:8091") as client:
        yield client
