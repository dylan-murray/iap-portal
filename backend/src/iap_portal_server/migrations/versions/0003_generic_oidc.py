"""Rename the configurable provider without changing identity or session ownership."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("user_identities", "auth_sessions"):
        table = sa.table(name, sa.column("provider", sa.String()))
        op.execute(table.update().where(table.c.provider == "okta").values(provider="oidc"))


def downgrade() -> None:
    for name in ("user_identities", "auth_sessions"):
        table = sa.table(name, sa.column("provider", sa.String()))
        op.execute(table.update().where(table.c.provider == "oidc").values(provider="okta"))
