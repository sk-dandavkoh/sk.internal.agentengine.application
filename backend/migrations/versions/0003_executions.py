"""Execution persistence: executions and node_executions.

Revision ID: 0003_executions
Revises: 0002_workflow_revision
Create Date: 2026-09-28

Phase 08: the runtime persists every execution/node transition. An
execution pins its immutable snapshot through (workflow_id, version) →
workflow_versions (RESTRICT). Node executions are rows so per-node state is
queryable (inspector, recovery); small value objects (policies, errors,
metadata) are JSONB documents. Purely additive: no existing data changes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_executions"
down_revision: str | None = "0002_workflow_revision"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EXECUTION_STATUSES = (
    "'created','queued','running','paused','waiting',"
    "'completed','failed','cancelled','timeout'"
)
_NODE_STATUSES = (
    "'pending','ready','running','completed','failed','skipped','cancelled'"
)


def upgrade() -> None:
    op.create_table(
        "executions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workflow_id", sa.String(), nullable=False),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column("retry_policy", postgresql.JSONB(), nullable=False),
        sa.Column("timeout_policy", postgresql.JSONB(), nullable=False),
        sa.Column("error", postgresql.JSONB(), nullable=True),
        sa.Column("cancellation", postgresql.JSONB(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["workflow_id", "workflow_version"],
            ["workflow_versions.workflow_id", "workflow_versions.version"],
            ondelete="RESTRICT",
            name="fk_executions_workflow_version",
        ),
        sa.CheckConstraint(
            f"status IN ({_EXECUTION_STATUSES})", name="ck_executions_status"
        ),
        sa.CheckConstraint("revision >= 1", name="ck_executions_revision_positive"),
    )
    op.create_index("ix_executions_workflow_id", "executions", ["workflow_id"])
    op.create_table(
        "node_executions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("execution_id", sa.String(), nullable=False),
        sa.Column("node_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("failures", postgresql.JSONB(), nullable=False),
        sa.Column("outputs", postgresql.JSONB(), nullable=True),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["execution_id"], ["executions.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            f"status IN ({_NODE_STATUSES})", name="ck_node_executions_status"
        ),
        sa.CheckConstraint(
            "attempt >= 0", name="ck_node_executions_attempt_non_negative"
        ),
    )
    op.create_index(
        "uq_node_executions_execution_node",
        "node_executions",
        ["execution_id", "node_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("node_executions")
    op.drop_table("executions")
