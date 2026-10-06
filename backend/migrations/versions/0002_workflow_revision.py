"""Workflow optimistic-concurrency revision and published-version counter.

Revision ID: 0002_workflow_revision
Revises: 0001_initial
Create Date: 2026-09-28

Phase 06: draft updates and publish are compare-and-swap on ``revision``
(no lost updates between concurrent editors / publishers). The counter
``current_version`` lets publish pick the next number without loading the
unbounded version history. Existing rows are backfilled from their
published versions, so the upgrade is safe on populated databases.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_workflow_revision"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "workflows",
        sa.Column("current_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.execute(
        "UPDATE workflows SET current_version = COALESCE("
        "(SELECT max(v.version) FROM workflow_versions v"
        " WHERE v.workflow_id = workflows.id), 0)"
    )
    op.create_check_constraint(
        "ck_workflows_revision_positive", "workflows", "revision >= 1"
    )
    op.create_check_constraint(
        "ck_workflows_current_version_non_negative", "workflows", "current_version >= 0"
    )


def downgrade() -> None:
    op.drop_constraint("ck_workflows_current_version_non_negative", "workflows")
    op.drop_constraint("ck_workflows_revision_positive", "workflows")
    op.drop_column("workflows", "current_version")
    op.drop_column("workflows", "revision")
