"""Versioned schema migrations.

    python -m iap_portal_server.db.migrate          # upgrade to the latest revision

The portal runs this at startup unless PORTAL_AUTO_MIGRATE=false. On Postgres a
transaction-scoped advisory lock serializes concurrent replicas.

Databases created by earlier releases with `create_all` have no `alembic_version`
table. They are checked against the baseline revision and adopted (stamped), then
upgraded normally. A database that matches neither is refused rather than altered.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from iap_portal_server.db import session as db_session

BASELINE_REVISION = "0001"
_ADVISORY_LOCK_KEY = 7243001

# Tables and columns created by the pre-migration `create_all` schema.
_LEGACY_SCHEMA = {
    "users": {"id", "email", "name", "picture_url", "is_admin", "created_at", "last_login_at"},
    "user_identities": {"id", "user_id", "provider", "subject", "raw_claims"},
    "groups": {"id", "name", "description"},
    "group_memberships": {"id", "user_id", "group_id", "added_at"},
    "apps": {
        "id", "slug", "display_name", "description", "icon_url", "upstream_service",
        "upstream_port", "health_check_path", "is_enabled", "created_at", "updated_at",
    },
    "app_owners": {"id", "app_id", "user_id"},
    "app_access": {
        "id", "app_id", "user_id", "group_id", "granted_by_user_id", "granted_at", "expires_at",
    },
    "audit_events": {
        "id", "at", "actor_email", "event_type", "app_slug", "target_email", "ip", "detail",
    },
    "access_requests": {
        "id", "app_id", "requester_user_id", "reason", "status", "requested_at",
        "decided_by_user_id", "decided_at", "decided_note",
    },
}


class SchemaError(RuntimeError):
    pass


def alembic_config() -> Config:
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parent.parent / "migrations")
    )
    return config


def head_revision() -> str:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def _adopt_legacy(sync_conn, config: Config) -> None:
    inspector = inspect(sync_conn)
    tables = set(inspector.get_table_names())
    if "alembic_version" in tables or not tables & _LEGACY_SCHEMA.keys():
        return
    problems = []
    for table, columns in _LEGACY_SCHEMA.items():
        if table not in tables:
            problems.append(f"missing table {table}")
            continue
        actual = {c["name"] for c in inspector.get_columns(table)}
        if actual != columns:
            problems.append(f"{table} columns differ: {sorted(actual ^ columns)}")
    if problems:
        raise SchemaError(
            "Existing database does not match the pre-migration schema; refusing to "
            "modify it. Back it up and migrate manually, or start from an empty "
            "database. Details: " + "; ".join(problems)
        )
    command.stamp(config, BASELINE_REVISION)


def _upgrade(sync_conn, revision: str) -> None:
    config = alembic_config()
    config.attributes["connection"] = sync_conn
    _adopt_legacy(sync_conn, config)
    command.upgrade(config, revision)


def _current(sync_conn) -> str | None:
    return MigrationContext.configure(sync_conn).get_current_revision()


async def migrate(engine: AsyncEngine | None = None, revision: str = "head") -> None:
    engine = engine or db_session.engine()
    async with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            await conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _ADVISORY_LOCK_KEY})
        await conn.run_sync(_upgrade, revision)


async def assert_current(engine: AsyncEngine | None = None) -> None:
    """Fail fast when auto-migration is disabled and the schema is behind."""
    engine = engine or db_session.engine()
    async with engine.connect() as conn:
        current = await conn.run_sync(_current)
    if current != head_revision():
        raise SchemaError(
            f"database schema is at {current or 'no revision'}, expected {head_revision()}; "
            "run `python -m iap_portal_server.db.migrate`"
        )


if __name__ == "__main__":
    asyncio.run(migrate())
