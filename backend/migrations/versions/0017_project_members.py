"""Project sharing: users a project's owner grants viewer or editor access.

Revision ID: 0017_project_members
Revises: 0016_user_profile
Create Date: 2026-10-06

A new table only: existing projects start unshared, so the upgrade never
widens access. Memberships go with their project (CASCADE); users are
disabled, never deleted (RESTRICT, like ``projects.owner_id``).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_project_members"
down_revision: str | None = "0016_user_profile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_members",
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("project_id", "user_id"),
        sa.CheckConstraint(
            "role IN ('viewer','editor')", name="ck_project_members_role"
        ),
    )
    # "Projects visible to me" looks memberships up by user.
    op.create_index("ix_project_members_user_id", "project_members", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_project_members_user_id", table_name="project_members")
    op.drop_table("project_members")
