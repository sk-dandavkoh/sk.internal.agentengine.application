"""Index executions by creation time.

Revision ID: 0013_executions_created_at_index
Revises: 0012_node_logs
Create Date: 2026-10-04

Frontend Phase 16: monitoring aggregates the executions created in a time
window, a range scan on ``created_at``. The cross-project listing (newest
first) sorts on the same column and uses the index too.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013_executions_created_at_index"
down_revision: str | None = "0012_node_logs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_executions_created_at", "executions", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_executions_created_at", table_name="executions")
