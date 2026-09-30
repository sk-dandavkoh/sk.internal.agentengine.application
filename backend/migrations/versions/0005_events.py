"""Event log.

Revision ID: 0005_events
Revises: 0004_execution_input
Create Date: 2026-09-30

Phase 11: append-only lifecycle events. ``position`` (identity) orders the
events of one execution/project; the (execution_id|project_id, position)
indexes serve "events of X after position N" reads. No foreign keys: events
are history, not state (see db_models.events). Purely additive.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_events"
down_revision: str | None = "0004_execution_input"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column(
            "position", sa.BigInteger(), sa.Identity(always=True), nullable=False
        ),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("execution_id", sa.String(), nullable=True),
        sa.Column("node_id", sa.String(), nullable=True),
        sa.Column("project_version", sa.Integer(), nullable=True),
        sa.Column("correlation_id", sa.String(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("position"),
        sa.UniqueConstraint("event_id"),
    )
    op.create_index(
        "ix_events_execution_position", "events", ["execution_id", "position"]
    )
    op.create_index("ix_events_project_position", "events", ["project_id", "position"])


def downgrade() -> None:
    op.drop_table("events")
