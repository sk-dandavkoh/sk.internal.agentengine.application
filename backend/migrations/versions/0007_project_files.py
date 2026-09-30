"""Project files.

Revision ID: 0007_project_files
Revises: 0006_credentials
Create Date: 2026-09-30

Phase 18: documentation, prompt templates, resources and assets travel with
a project (project packages). Stored as ``{path: base64}`` documents on the
draft and on every published version (immutable snapshot). Existing rows
had no files; the server default backfills ``{}``, which is exactly that.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_project_files"
down_revision: str | None = "0006_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table, column in (("projects", "draft_files"), ("project_versions", "files")):
        op.add_column(
            table,
            sa.Column(
                column,
                postgresql.JSONB(),
                server_default=sa.text("'{}'::jsonb"),
                nullable=False,
            ),
        )


def downgrade() -> None:
    op.drop_column("project_versions", "files")
    op.drop_column("projects", "draft_files")
