"""SQLAlchemy table definitions for the persistence schema (Phase 05).

Domain objects are *not* ORM-mapped (architecture §79): these are plain
Table definitions used by the repositories through SQLAlchemy Core, so no
ORM identity-map state can leak into the domain layer. Graph, triggers and
dependencies live in JSONB columns — they are structurally opaque documents;
semantic validation happens in the domain, not in SQL.
"""

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
)
from sqlalchemy.dialects.postgresql import JSONB

JSONB_VARIANT = (
    JSON().with_variant(JSONB, "postgresql")  # portable outside tests (SQLite)
)

metadata = MetaData()

projects = Table(
    "projects",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("description", String, nullable=False, server_default=""),
    Column("status", String, nullable=False),
    Column("draft_graph", JSONB_VARIANT, nullable=False),
    Column("draft_triggers", JSONB_VARIANT, nullable=False),
    Column("draft_dependencies", JSONB_VARIANT, nullable=False),
    # Optimistic-concurrency token; every successful write increments it.
    Column("revision", Integer, nullable=False, server_default="1"),
    Column("current_version", Integer, nullable=False, server_default="0"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("revision >= 1", name="ck_projects_revision_positive"),
    CheckConstraint(
        "current_version >= 0", name="ck_projects_current_version_non_negative"
    ),
)

project_versions = Table(
    "project_versions",
    metadata,
    Column("id", String, primary_key=True),
    Column(
        "project_id",
        String,
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("version", Integer, nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("graph", JSONB_VARIANT, nullable=False),
    Column("triggers", JSONB_VARIANT, nullable=False),
    Column("dependencies", JSONB_VARIANT, nullable=False),
    Column("description", String, nullable=False, server_default=""),
    Column("published_at", DateTime(timezone=True), nullable=False),
    Index("uq_project_versions_project_version", "project_id", "version", unique=True),
    Index("ix_project_versions_project_id", "project_id"),
)

_EXECUTION_STATUSES = (
    "'created','queued','running','paused','waiting',"
    "'completed','failed','cancelled','timeout'"
)
_NODE_STATUSES = (
    "'pending','ready','running','completed','failed','skipped','cancelled'"
)

executions = Table(
    "executions",
    metadata,
    Column("id", String, primary_key=True),
    Column("project_id", String, nullable=False),
    Column("project_version", Integer, nullable=False),
    Column("status", String, nullable=False),
    # Small value objects as documents: metadata, policies, error, cancel.
    Column("metadata", JSONB_VARIANT, nullable=False),
    Column("retry_policy", JSONB_VARIANT, nullable=False),
    Column("timeout_policy", JSONB_VARIANT, nullable=False),
    Column("error", JSONB_VARIANT, nullable=True),
    Column("cancellation", JSONB_VARIANT, nullable=True),
    Column("revision", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    # Pins the immutable snapshot; RESTRICT keeps history reproducible.
    ForeignKeyConstraint(
        ["project_id", "project_version"],
        ["project_versions.project_id", "project_versions.version"],
        ondelete="RESTRICT",
        name="fk_executions_project_version",
    ),
    CheckConstraint(f"status IN ({_EXECUTION_STATUSES})", name="ck_executions_status"),
    CheckConstraint("revision >= 1", name="ck_executions_revision_positive"),
    Index("ix_executions_project_id", "project_id"),
)

node_executions = Table(
    "node_executions",
    metadata,
    Column("id", String, primary_key=True),
    Column(
        "execution_id",
        String,
        ForeignKey("executions.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("node_id", String, nullable=False),
    Column("status", String, nullable=False),
    Column("attempt", Integer, nullable=False),
    Column("failures", JSONB_VARIANT, nullable=False),
    Column("outputs", JSONB_VARIANT, nullable=True),
    Column("retry_at", DateTime(timezone=True), nullable=True),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    CheckConstraint(f"status IN ({_NODE_STATUSES})", name="ck_node_executions_status"),
    CheckConstraint("attempt >= 0", name="ck_node_executions_attempt_non_negative"),
    Index("uq_node_executions_execution_node", "execution_id", "node_id", unique=True),
)
