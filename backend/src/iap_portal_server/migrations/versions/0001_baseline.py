"""Baseline: the schema created by `create_all` before versioned migrations.

Existing databases without an `alembic_version` table are checked against this
revision and stamped (see iap_portal_server.db.migrate), then upgraded.

Revision ID: 0001
Revises:
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _ts(name: str, **kw) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), **kw)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("name", sa.String(255)),
        sa.Column("picture_url", sa.String(1024)),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        _ts("created_at", server_default=sa.func.now(), nullable=False),
        _ts("last_login_at"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "user_identities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("raw_claims", sa.JSON(), nullable=False),
        sa.UniqueConstraint("provider", "subject", name="uq_provider_subject"),
    )

    op.create_table(
        "groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(500)),
    )
    op.create_index("ix_groups_name", "groups", ["name"], unique=True)

    op.create_table(
        "group_memberships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("groups.id"), nullable=False),
        _ts("added_at", server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "group_id", name="uq_user_group"),
    )

    op.create_table(
        "apps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(63), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(2000)),
        sa.Column("icon_url", sa.String(1024)),
        sa.Column("upstream_service", sa.String(512), nullable=False),
        sa.Column("upstream_port", sa.Integer(), nullable=False),
        sa.Column("health_check_path", sa.String(255), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        _ts("created_at", server_default=sa.func.now(), nullable=False),
        _ts("updated_at", server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_apps_slug", "apps", ["slug"], unique=True)

    op.create_table(
        "app_owners",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("app_id", sa.Integer(), sa.ForeignKey("apps.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.UniqueConstraint("app_id", "user_id", name="uq_app_owner"),
    )

    op.create_table(
        "app_access",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("app_id", sa.Integer(), sa.ForeignKey("apps.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("groups.id")),
        sa.Column("granted_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        _ts("granted_at", server_default=sa.func.now(), nullable=False),
        _ts("expires_at"),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        _ts("at", server_default=sa.func.now(), nullable=False),
        sa.Column("actor_email", sa.String(320)),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("app_slug", sa.String(63)),
        sa.Column("target_email", sa.String(320)),
        sa.Column("ip", sa.String(64)),
        sa.Column("detail", sa.JSON(), nullable=False),
    )
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])
    op.create_index("ix_audit_events_app_slug", "audit_events", ["app_slug"])

    op.create_table(
        "access_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("app_id", sa.Integer(), sa.ForeignKey("apps.id"), nullable=False),
        sa.Column("requester_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reason", sa.String(1000)),
        sa.Column("status", sa.String(20), nullable=False),
        _ts("requested_at", server_default=sa.func.now(), nullable=False),
        sa.Column("decided_by_user_id", sa.Integer(), sa.ForeignKey("users.id")),
        _ts("decided_at"),
        sa.Column("decided_note", sa.String(1000)),
    )
    op.create_index("ix_access_requests_app_id", "access_requests", ["app_id"])
    op.create_index("ix_access_requests_requester_user_id", "access_requests", ["requester_user_id"])
    op.create_index("ix_access_requests_status", "access_requests", ["status"])


def downgrade() -> None:
    for table in (
        "access_requests", "audit_events", "app_access", "app_owners", "apps",
        "group_memberships", "groups", "user_identities", "users",
    ):
        op.drop_table(table)
