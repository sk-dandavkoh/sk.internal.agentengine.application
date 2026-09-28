"""Execution start payload.

Revision ID: 0004_execution_input
Revises: 0003_executions
Create Date: 2026-09-28

Phase 09: ``core.input`` reads the JSON object an execution was started
with, so the payload is part of the execution record (reproducibility: the
same version + the same input). Existing executions were started without
input; the server default backfills them with ``{}``, which is exactly what
they ran with. Resolved node versions go into the existing ``metadata``
document and need no schema change.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_execution_input"
down_revision: str | None = "0003_executions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "executions",
        sa.Column(
            "input",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("executions", "input")
