"""Personal access tokens.

Revision ID: 0015_access_tokens
Revises: 0014_version_plugin_versions
Create Date: 2026-10-06

Long-lived, named, individually revocable credentials for scripts and AI
agents (docs/mcp.md §3). Only the SHA-256 of a token is stored. Tokens go
with their user (CASCADE); users are disabled, not deleted, and a disabled
user's tokens stop working (checked at authentication).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_access_tokens"
down_revision: str | None = "0014_version_plugin_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "access_tokens",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("hint", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_access_tokens_user_id", "access_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_access_tokens_user_id", table_name="access_tokens")
    op.drop_table("access_tokens")
