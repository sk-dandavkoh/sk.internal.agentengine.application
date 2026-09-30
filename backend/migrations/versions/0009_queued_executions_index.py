"""Partial index for queue recovery.

Revision ID: 0009_queued_executions_index
Revises: 0008_auth
Create Date: 2026-09-30

Phase 22: workers periodically re-dispatch executions that stayed QUEUED
(``ExecutionService.dispatch_queued``). The partial index covers only QUEUED
rows, so the scan costs the size of the backlog, not of the history.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_queued_executions_index"
down_revision: str | None = "0008_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_executions_queued_created_at",
        "executions",
        ["created_at"],
        postgresql_where=sa.text("status = 'queued'"),
    )


def downgrade() -> None:
    op.drop_index("ix_executions_queued_created_at", table_name="executions")
