"""Users, sessions and workflow ownership.

Revision ID: 0008_auth
Revises: 0007_workflow_files
Create Date: 2026-09-30

Phase 20: local accounts and server-side sessions. Existing workflows get no
owner (NULL), which makes them admin-only: upgrading never widens access.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_auth"
down_revision: str | None = "0007_workflow_files"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("disabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('admin','member')", name="ck_users_role"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_table(
        "sessions",
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])
    op.add_column("workflows", sa.Column("owner_id", sa.String(), nullable=True))
    op.create_foreign_key(
        "fk_workflows_owner_id",
        "workflows",
        "users",
        ["owner_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_workflows_owner_id", "workflows", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_workflows_owner_id", table_name="workflows")
    op.drop_constraint("fk_workflows_owner_id", "workflows", type_="foreignkey")
    op.drop_column("workflows", "owner_id")
    op.drop_table("sessions")
    op.drop_table("users")
