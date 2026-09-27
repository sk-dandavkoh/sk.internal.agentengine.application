"""Initial persistence schema: projects and project_versions.

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-27

Phase 05 scope: Project identity + mutable draft, immutable published
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
        "projects",
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
        "project_versions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("graph", postgresql.JSONB(), nullable=False),
        sa.Column("triggers", postgresql.JSONB(), nullable=False),
        sa.Column("dependencies", postgresql.JSONB(), nullable=False),
        sa.Column("description", sa.String(), server_default="", nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_project_versions_project_version",
        "project_versions",
        ["project_id", "version"],
        unique=True,
    )
    op.create_index(
        "ix_project_versions_project_id", "project_versions", ["project_id"]
    )


def downgrade() -> None:
    op.drop_table("project_versions")
    op.drop_table("projects")
