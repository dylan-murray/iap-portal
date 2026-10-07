"""Exercise migrations, sessions and expiring grants on an isolated Postgres schema."""

import os
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from iap_portal_server.auth.session import (
    create_app_session, create_portal_session, load_app_session, utcnow,
)
from iap_portal_server.db.migrate import assert_current, migrate
from iap_portal_server.db.models import App, AppAccess, AuthSession, User, UserIdentity
from iap_portal_server.rbac.checks import user_can_access_app

URL = os.environ.get('IAP_TEST_POSTGRES_URL', '')
pytestmark = pytest.mark.skipif(not URL, reason='IAP_TEST_POSTGRES_URL is not configured')


async def test_postgres_upgrade_sessions_and_expiring_grants():
    schema = 'iap_test_' + uuid4().hex
    admin = create_async_engine(URL)
    engine = create_async_engine(URL, connect_args={'server_settings': {'search_path': schema}})
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        await migrate(engine, '0002')
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            user = User(email='fixture@example.com')
            app = App(slug='fixture', display_name='Fixture', upstream_service='fixture')
            db.add_all([user, app])
            await db.flush()
            db.add(UserIdentity(user_id=user.id, provider='okta', issuer='https://idp.example.test',
                                subject='fixture', raw_claims={}))
            _, parent = await create_portal_session(db, user, 'okta')
            app_token, _ = await create_app_session(db, parent, app.slug)
            grant = AppAccess(app_id=app.id, user_id=user.id, granted_by_user_id=user.id,
                              expires_at=utcnow() + timedelta(minutes=10))
            db.add(grant)
            await db.commit()
            parent_id, grant_id = parent.id, grant.id
        await migrate(engine)
        await migrate(engine)
        await assert_current(engine)
        async with sessions() as db:
            identity = (await db.execute(select(UserIdentity))).scalar_one()
            assert identity.provider == 'oidc' and identity.subject == 'fixture'
            loaded = await load_app_session(db, app_token, 'fixture')
            assert loaded is not None
            app = (await db.execute(select(App))).scalar_one()
            assert await user_can_access_app(db, loaded[1], app)
            grant = await db.get(AppAccess, grant_id)
            grant.expires_at = utcnow() - timedelta(minutes=1)
            await db.commit()
            assert not await user_can_access_app(db, loaded[1], app)
            parent = await db.get(AuthSession, parent_id)
            assert parent.provider == 'oidc'
            parent.revoked_at = utcnow()
            await db.commit()
            assert await load_app_session(db, app_token, 'fixture') is None
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
