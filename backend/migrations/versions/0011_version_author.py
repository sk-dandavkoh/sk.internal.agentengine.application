"""Record who published a project version.

Revision ID: 0011_version_author
Revises: 0010_execution_heartbeat
Create Date: 2026-10-02

Frontend Phase 12 shows a version's author. Existing versions keep NULL:
their publisher was never recorded and is not guessed from the owner.
Users are disabled, never deleted, so RESTRICT keeps the reference valid.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_version_author"
down_revision: str | None = "0010_execution_heartbeat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "project_versions", sa.Column("published_by", sa.String(), nullable=True)
    )
    op.create_foreign_key(
        "fk_project_versions_published_by",
        "project_versions",
        "users",
        ["published_by"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_project_versions_published_by", "project_versions", type_="foreignkey"
    )
    op.drop_column("project_versions", "published_by")
