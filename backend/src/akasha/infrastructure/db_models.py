"""SQLAlchemy table definitions for the persistence schema (Phase 05).

Domain objects are *not* ORM-mapped (architecture §79): these are plain
Table definitions used by the repositories through SQLAlchemy Core, so no
ORM identity-map state can leak into the domain layer. Graph, triggers and
dependencies live in JSONB columns — they are structurally opaque documents;
semantic validation happens in the domain, not in SQL.
"""

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
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
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
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
