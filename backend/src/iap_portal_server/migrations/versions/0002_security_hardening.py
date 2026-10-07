"""Server-side sessions, app sign-in codes, issuer-bound identities, rate limits.

Existing users, grants, and identities are preserved. Identities gain an `issuer`
column (NULL for existing rows until their next sign-in). Existing browser
sessions were stateless signed cookies and are not carried over: users sign in
again after the upgrade.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("disabled_at", sa.DateTime(timezone=True)))

    with op.batch_alter_table("user_identities") as batch:
        batch.add_column(sa.Column("issuer", sa.String(512)))
        batch.drop_constraint("uq_provider_subject", type_="unique")
        batch.create_unique_constraint("uq_identity_issuer_subject", ["issuer", "subject"])

    with op.batch_alter_table("app_access") as batch:
        batch.alter_column("granted_by_user_id", existing_type=sa.Integer(), nullable=True)

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("app_slug", sa.String(63)),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("auth_sessions.id")),
        sa.Column("provider", sa.String(32)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_auth_sessions_token_hash", "auth_sessions", ["token_hash"], unique=True)
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_parent_id", "auth_sessions", ["parent_id"])

    op.create_table(
        "app_login_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("auth_sessions.id"), nullable=False),
        sa.Column("app_slug", sa.String(63), nullable=False),
        sa.Column("return_path", sa.String(4096), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_app_login_codes_code_hash", "app_login_codes", ["code_hash"], unique=True)

    op.create_table(
        "rate_limit_counters",
        sa.Column("key", sa.String(200), primary_key=True),
        sa.Column("window_start", sa.BigInteger(), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("rate_limit_counters")
    op.drop_table("app_login_codes")
    op.drop_table("auth_sessions")
    with op.batch_alter_table("app_access") as batch:
        batch.alter_column("granted_by_user_id", existing_type=sa.Integer(), nullable=False)
    with op.batch_alter_table("user_identities") as batch:
        batch.drop_constraint("uq_identity_issuer_subject", type_="unique")
        batch.create_unique_constraint("uq_provider_subject", ["provider", "subject"])
        batch.drop_column("issuer")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("disabled_at")
