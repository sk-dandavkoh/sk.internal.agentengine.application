"""Execution owner heartbeat for stale-execution recovery.

Revision ID: 0010_execution_heartbeat
Revises: 0009_queued_executions_index
Create Date: 2026-09-30

Phase 23: the runtime owning a started execution refreshes ``heartbeat_at``
(database clock); started rows whose heartbeat is older than the stale
threshold are recovered. Existing rows get the migration time: an execution
left RUNNING by a process that died before the upgrade becomes stale one
threshold after it, and is then recovered like any other.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_execution_heartbeat"
down_revision: str | None = "0009_queued_executions_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # now() is stable within the statement: PostgreSQL fills existing rows
    # without rewriting the table.
    op.add_column(
        "executions",
        sa.Column(
            "heartbeat_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_executions_started_heartbeat_at",
        "executions",
        ["heartbeat_at"],
        postgresql_where=sa.text("status IN ('running','paused','waiting')"),
    )


def downgrade() -> None:
    op.drop_index("ix_executions_started_heartbeat_at", table_name="executions")
    op.drop_column("executions", "heartbeat_at")
