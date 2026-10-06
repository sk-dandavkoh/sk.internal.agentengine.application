"""Workflow sharing: users a workflow's owner grants viewer or editor access.

Revision ID: 0017_workflow_members
Revises: 0016_user_profile
Create Date: 2026-10-06

A new table only: existing workflows start unshared, so the upgrade never
widens access. Memberships go with their workflow (CASCADE); users are
disabled, never deleted (RESTRICT, like ``workflows.owner_id``).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_workflow_members"
down_revision: str | None = "0016_user_profile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_members",
        sa.Column("workflow_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("workflow_id", "user_id"),
        sa.CheckConstraint(
            "role IN ('viewer','editor')", name="ck_workflow_members_role"
        ),
    )
    # "Workflows visible to me" looks memberships up by user.
    op.create_index("ix_workflow_members_user_id", "workflow_members", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_workflow_members_user_id", table_name="workflow_members")
    op.drop_table("workflow_members")
