"""Waiting nodes: a node attempt may wait for outside input (core.human).

Revision ID: 0018_waiting_nodes
Revises: 0017_workflow_members
Create Date: 2026-10-06

Additive: a ``waiting`` node status, the wait request and the response on
node executions, and ``executions.wake_at`` (with a partial index over
WAITING rows) for wait deadlines. Existing rows keep NULLs, which mean "not
waiting"; no existing status changes meaning. Downgrade refuses while any
node is waiting, rather than rewriting a state it cannot represent.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0018_waiting_nodes"
down_revision: str | None = "0017_workflow_members"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "'pending','ready','running','completed','failed','skipped','cancelled'"
_NEW = (
    "'pending','ready','running','waiting','completed','failed','skipped','cancelled'"
)


def upgrade() -> None:
    op.drop_constraint("ck_node_executions_status", "node_executions", type_="check")
    op.create_check_constraint(
        "ck_node_executions_status", "node_executions", f"status IN ({_NEW})"
    )
    op.add_column("node_executions", sa.Column("wait", JSONB(), nullable=True))
    op.add_column("node_executions", sa.Column("resume", JSONB(), nullable=True))
    op.add_column(
        "executions", sa.Column("wake_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "ix_executions_waiting_wake_at",
        "executions",
        ["wake_at"],
        postgresql_where=sa.text("status = 'waiting'"),
    )


def downgrade() -> None:
    waiting = op.get_bind().execute(
        sa.text("SELECT count(*) FROM node_executions WHERE status = 'waiting'")
    )
    if waiting.scalar_one():
        raise RuntimeError(
            "cannot downgrade: node executions are waiting; answer or cancel"
            " their executions first"
        )
    op.drop_index("ix_executions_waiting_wake_at", table_name="executions")
    op.drop_column("executions", "wake_at")
    op.drop_column("node_executions", "resume")
    op.drop_column("node_executions", "wait")
    op.drop_constraint("ck_node_executions_status", "node_executions", type_="check")
    op.create_check_constraint(
        "ck_node_executions_status", "node_executions", f"status IN ({_OLD})"
    )
