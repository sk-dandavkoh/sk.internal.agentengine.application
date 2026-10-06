"""Display names and last-login times for user administration.

Revision ID: 0016_user_profile
Revises: 0015_access_tokens
Create Date: 2026-10-06

Both nullable: existing accounts have no display name (the UI shows the
username) and their last login was never recorded (not guessed from
sessions, which are purged on expiry).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_user_profile"
down_revision: str | None = "0015_access_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("display_name", sa.String(), nullable=True))
    op.add_column(
        "users",
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "display_name")
