"""Versioned migrations: fresh installs, legacy create_all databases, refusal."""

from __future__ import annotations

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from iap_portal_server.db.migrate import SchemaError, assert_current, head_revision, migrate
from iap_portal_server.db.models import Base


async def _engine(tmp_path, name="db.sqlite"):
    return create_async_engine(f"sqlite+aiosqlite:///{tmp_path / name}")


async def _diff(engine):
    async with engine.connect() as conn:
        return await conn.run_sync(
            lambda c: compare_metadata(MigrationContext.configure(c, opts={"compare_type": True}), Base.metadata)
        )


async def test_fresh_database_matches_models(tmp_path):
    engine = await _engine(tmp_path)
    await migrate(engine)
    assert await _diff(engine) == []
    await assert_current(engine)
    await migrate(engine)  # idempotent
    await engine.dispose()


async def test_legacy_database_is_adopted_and_data_preserved(tmp_path):
    engine = await _engine(tmp_path)
    await migrate(engine, "0001")
    async with engine.begin() as conn:
        # Simulate a pre-migration create_all database: no alembic_version table.
        await conn.execute(text("DROP TABLE alembic_version"))
        await conn.execute(text(
            "INSERT INTO users (id, email, is_admin, created_at) VALUES (1, 'a@example.com', 0, CURRENT_TIMESTAMP)"
        ))
        await conn.execute(text(
            "INSERT INTO user_identities (user_id, provider, subject, raw_claims) VALUES (1, 'okta', 'u1', '{}')"
        ))
        await conn.execute(text(
            "INSERT INTO apps (id, slug, display_name, upstream_service, upstream_port, health_check_path,"
            " is_enabled, created_at, updated_at) VALUES (1, 'x', 'X', 's', 80, '/h', 1, CURRENT_TIMESTAMP,"
            " CURRENT_TIMESTAMP)"
        ))
        await conn.execute(text(
            "INSERT INTO app_access (app_id, user_id, granted_by_user_id, granted_at) VALUES (1, 1, 1, CURRENT_TIMESTAMP)"
        ))
    await migrate(engine)
    assert await _diff(engine) == []
    async with engine.connect() as conn:
        identity = (await conn.execute(text("SELECT provider, subject, issuer FROM user_identities"))).one()
        assert tuple(identity) == ("oidc", "u1", None)
        assert (await conn.execute(text("SELECT count(*) FROM app_access"))).scalar_one() == 1
        version = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
        assert version == head_revision()
    await engine.dispose()


async def test_unknown_existing_schema_is_refused(tmp_path):
    engine = await _engine(tmp_path)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)"))
    with pytest.raises(SchemaError):
        await migrate(engine)
    async with engine.connect() as conn:
        tables = (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).scalars().all()
    assert tables == ["users"]
    await engine.dispose()


async def test_startup_refuses_outdated_schema_when_auto_migrate_is_off(tmp_path):
    engine = await _engine(tmp_path)
    await migrate(engine, "0001")
    with pytest.raises(SchemaError):
        await assert_current(engine)
    await engine.dispose()


async def test_oidc_migration_preserves_identities_and_sessions(tmp_path):
    engine = await _engine(tmp_path)
    await migrate(engine, '0002')
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO users (id, email, is_admin, created_at) VALUES (1, 'a@example.com', 0, CURRENT_TIMESTAMP)"
        ))
        for provider in ('okta', 'google'):
            await conn.execute(text(
                "INSERT INTO user_identities (user_id, provider, subject, issuer, raw_claims) "
                "VALUES (1, :provider, 'subject', :issuer, '{}')"
            ), {'provider': provider, 'issuer': f'https://{provider}.example.test'})
            await conn.execute(text(
                "INSERT INTO auth_sessions (token_hash, kind, user_id, provider, expires_at) "
                "VALUES (:provider, 'portal', 1, :provider, '2030-01-01')"
            ), {'provider': provider})
    await migrate(engine)
    await migrate(engine)
    async with engine.connect() as conn:
        identities = (await conn.execute(text(
            'SELECT user_id, provider, subject, issuer FROM user_identities ORDER BY provider'
        ))).all()
        assert [tuple(row) for row in identities] == [
            (1, 'google', 'subject', 'https://google.example.test'),
            (1, 'oidc', 'subject', 'https://okta.example.test'),
        ]
        sessions = (await conn.execute(text(
            'SELECT user_id, token_hash, provider FROM auth_sessions ORDER BY provider'
        ))).all()
        assert [tuple(row) for row in sessions] == [(1, 'google', 'google'), (1, 'okta', 'oidc')]
    await engine.dispose()
