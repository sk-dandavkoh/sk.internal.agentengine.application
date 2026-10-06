"""Initial persistence schema: workflows and workflow_versions.

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-27

Phase 05 scope: Workflow identity + mutable draft, immutable published
versions. Graph/triggers/dependencies are JSONB documents — opaquely stored
and round-tripped, semantically validated only in the domain layer.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflows",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), server_default="", nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("draft_graph", postgresql.JSONB(), nullable=False),
        sa.Column("draft_triggers", postgresql.JSONB(), nullable=False),
        sa.Column("draft_dependencies", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "workflow_versions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workflow_id", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("graph", postgresql.JSONB(), nullable=False),
        sa.Column("triggers", postgresql.JSONB(), nullable=False),
        sa.Column("dependencies", postgresql.JSONB(), nullable=False),
        sa.Column("description", sa.String(), server_default="", nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_workflow_versions_workflow_version",
        "workflow_versions",
        ["workflow_id", "version"],
        unique=True,
    )
    op.create_index(
        "ix_workflow_versions_workflow_id", "workflow_versions", ["workflow_id"]
    )


def downgrade() -> None:
    op.drop_table("workflow_versions")
    op.drop_table("workflows")
