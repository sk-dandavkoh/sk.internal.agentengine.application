"""Persist node log lines.

Revision ID: 0012_node_logs
Revises: 0011_version_author
Create Date: 2026-10-04

Frontend Phase 15 (Execution Inspector) shows what a node logged. Until now
those lines reached only the process log. Rows are written by the runtime in
the same transaction as the attempt's outcome and deleted with their
execution. Executions that ran before this migration have no stored lines.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_node_logs"
down_revision: str | None = "0011_version_author"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "node_logs",
        sa.Column(
            "position", sa.BigInteger(), sa.Identity(always=True), primary_key=True
        ),
        sa.Column(
            "execution_id",
            sa.String(),
            sa.ForeignKey("executions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_id", sa.String(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("level", sa.String(), nullable=False),
        sa.Column("message", sa.String(), nullable=False),
        sa.Column("logged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fields", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("attempt >= 1", name="ck_node_logs_attempt"),
        sa.CheckConstraint(
            "level IN ('DEBUG','INFO','WARNING','ERROR','CRITICAL')",
            name="ck_node_logs_level",
        ),
    )
    op.create_index(
        "ix_node_logs_execution_position", "node_logs", ["execution_id", "position"]
    )


def downgrade() -> None:
    op.drop_index("ix_node_logs_execution_position", table_name="node_logs")
    op.drop_table("node_logs")
