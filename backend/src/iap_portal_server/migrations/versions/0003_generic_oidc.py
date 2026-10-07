"""Rename the configurable provider without changing identity or session ownership."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("user_identities", "auth_sessions"):
        op.execute(sa.text(f"UPDATE {table} SET provider = 'oidc' WHERE provider = 'okta'"))


def downgrade() -> None:
    for table in ("user_identities", "auth_sessions"):
        op.execute(sa.text(f"UPDATE {table} SET provider = 'okta' WHERE provider = 'oidc'"))
