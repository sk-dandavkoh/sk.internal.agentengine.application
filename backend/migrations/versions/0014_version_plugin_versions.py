"""Pin the plugin versions a workflow version was published against.

Revision ID: 0014_version_plugin_versions
Revises: 0013_executions_created_at_index
Create Date: 2026-10-06

A published version must not change behavior when a newer plugin version
is installed (docs/mcp.md, plugin reproducibility). Existing
versions get ``{}``: which plugin versions they were published against was
never recorded and is not guessed; they keep running on whatever version
is installed, as before.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_version_plugin_versions"
down_revision: str | None = "0013_executions_created_at_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_versions",
        sa.Column(
            "plugin_versions",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("workflow_versions", "plugin_versions")
