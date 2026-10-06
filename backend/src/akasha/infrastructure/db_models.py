"""SQLAlchemy table definitions for the persistence schema (Phase 05).

Domain objects are *not* ORM-mapped (architecture §79): these are plain
Table definitions used by the repositories through SQLAlchemy Core, so no
ORM identity-map state can leak into the domain layer. Graph, triggers and
dependencies live in JSONB columns — they are structurally opaque documents;
semantic validation happens in the domain, not in SQL.
"""

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

JSONB_VARIANT = (
    JSON().with_variant(JSONB, "postgresql")  # portable outside tests (SQLite)
)

metadata = MetaData()

# Authentication (Phase 20). Users are never deleted while they own
# workflows (RESTRICT); disabling is the revocation path.
users = Table(
    "users",
    metadata,
    Column("id", String, primary_key=True),
    Column("username", String, nullable=False, unique=True),
    Column("password_hash", String, nullable=False),
    Column("role", String, nullable=False),
    Column("disabled", Boolean, nullable=False, server_default="false"),
    Column("display_name", String, nullable=True),
    Column("last_login_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("role IN ('admin','member')", name="ck_users_role"),
)

# Only the SHA-256 of a session token is stored: a database leak does not
# hand out live sessions.
sessions = Table(
    "sessions",
    metadata,
    Column("token_hash", String, primary_key=True),
    Column(
        "user_id", String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    ),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Index("ix_sessions_user_id", "user_id"),
    Index("ix_sessions_expires_at", "expires_at"),
)

access_tokens = Table(
    "access_tokens",
    metadata,
    Column("id", String, primary_key=True),
    Column("token_hash", String, nullable=False, unique=True),
    Column(
        "user_id", String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    ),
    Column("name", String, nullable=False),
    Column("hint", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=True),
    Column("last_used_at", DateTime(timezone=True), nullable=True),
    Index("ix_access_tokens_user_id", "user_id"),
)

workflows = Table(
    "workflows",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("description", String, nullable=False, server_default=""),
    Column("status", String, nullable=False),
    Column("draft_graph", JSONB_VARIANT, nullable=False),
    Column("draft_triggers", JSONB_VARIANT, nullable=False),
    Column("draft_dependencies", JSONB_VARIANT, nullable=False),
    # {path: base64 content}; see domain.workflow.validate_workflow_files.
    Column("draft_files", JSONB_VARIANT, nullable=False, server_default="{}"),
    # Optimistic-concurrency token; every successful write increments it.
    Column("revision", Integer, nullable=False, server_default="1"),
    Column("current_version", Integer, nullable=False, server_default="0"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    # NULL = admin-only (workflows from before accounts existed).
    Column(
        "owner_id", String, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    ),
    Index("ix_workflows_owner_id", "owner_id"),
    CheckConstraint("revision >= 1", name="ck_workflows_revision_positive"),
    CheckConstraint(
        "current_version >= 0", name="ck_workflows_current_version_non_negative"
    ),
)

# Workflow sharing (migration 0017). The owner is not a row here.
workflow_members = Table(
    "workflow_members",
    metadata,
    Column(
        "workflow_id",
        String,
        ForeignKey("workflows.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "user_id", String, ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    ),
    Column("role", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Index("ix_workflow_members_user_id", "user_id"),
    CheckConstraint("role IN ('viewer','editor')", name="ck_workflow_members_role"),
)

workflow_versions = Table(
    "workflow_versions",
    metadata,
    Column("id", String, primary_key=True),
    Column(
        "workflow_id",
        String,
        ForeignKey("workflows.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("version", Integer, nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("graph", JSONB_VARIANT, nullable=False),
    Column("triggers", JSONB_VARIANT, nullable=False),
    Column("dependencies", JSONB_VARIANT, nullable=False),
    Column("description", String, nullable=False, server_default=""),
    Column("published_at", DateTime(timezone=True), nullable=False),
    Column("files", JSONB_VARIANT, nullable=False, server_default="{}"),
    Column(
        "published_by",
        String,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    Column("plugin_versions", JSONB_VARIANT, nullable=False, server_default="{}"),
    Index(
        "uq_workflow_versions_workflow_version", "workflow_id", "version", unique=True
    ),
    Index("ix_workflow_versions_workflow_id", "workflow_id"),
)

_EXECUTION_STATUSES = (
    "'created','queued','running','paused','waiting',"
    "'completed','failed','cancelled','timeout'"
)
_NODE_STATUSES = (
    "'pending','ready','running','waiting','completed','failed','skipped','cancelled'"
)

executions = Table(
    "executions",
    metadata,
    Column("id", String, primary_key=True),
    Column("workflow_id", String, nullable=False),
    Column("workflow_version", Integer, nullable=False),
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
    # Start payload (JSON object); written once at creation.
    Column("input", JSONB_VARIANT, nullable=False, server_default="{}"),
    # Owner liveness, database clock (Phase 23): refreshed by every save and
    # by the owning runtime's heartbeat; stale started rows are recovered.
    Column(
        "heartbeat_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    # WAITING only (migration 0018): when the execution must be settled
    # without a response (a wait deadline or the execution deadline).
    Column("wake_at", DateTime(timezone=True), nullable=True),
    # Pins the immutable snapshot; RESTRICT keeps history reproducible.
    ForeignKeyConstraint(
        ["workflow_id", "workflow_version"],
        ["workflow_versions.workflow_id", "workflow_versions.version"],
        ondelete="RESTRICT",
        name="fk_executions_workflow_version",
    ),
    CheckConstraint(f"status IN ({_EXECUTION_STATUSES})", name="ck_executions_status"),
    CheckConstraint("revision >= 1", name="ck_executions_revision_positive"),
    Index("ix_executions_workflow_id", "workflow_id"),
    # Monitoring windows and the newest-first listing (migration 0013).
    Index("ix_executions_created_at", "created_at"),
    # Queue recovery scans QUEUED executions; the partial index stays as
    # small as the backlog however large the history grows.
    Index(
        "ix_executions_queued_created_at",
        "created_at",
        postgresql_where=text("status = 'queued'"),
    ),
    # Stale-execution recovery scans only started rows.
    Index(
        "ix_executions_started_heartbeat_at",
        "heartbeat_at",
        postgresql_where=text("status IN ('running','paused','waiting')"),
    ),
    # Due waits are scanned by wake time among WAITING rows only.
    Index(
        "ix_executions_waiting_wake_at",
        "wake_at",
        postgresql_where=text("status = 'waiting'"),
    ),
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
    # The last wait request and the response the next attempt receives.
    Column("wait", JSONB_VARIANT, nullable=True),
    Column("resume", JSONB_VARIANT, nullable=True),
    Column("retry_at", DateTime(timezone=True), nullable=True),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    CheckConstraint(f"status IN ({_NODE_STATUSES})", name="ck_node_executions_status"),
    CheckConstraint("attempt >= 0", name="ck_node_executions_attempt_non_negative"),
    Index("uq_node_executions_execution_node", "execution_id", "node_id", unique=True),
)

# Append-only event log (Phase 11). Deliberately no foreign keys: events are
# history and must stay readable (audit) even where retention later prunes
# the rows they describe; ids are plain references.
events = Table(
    "events",
    metadata,
    # Store-assigned order. Per execution/workflow it is the true event order
    # (one CAS-guarded writer, rows inserted in order).
    Column("position", BigInteger, Identity(always=True), primary_key=True),
    Column("event_id", String, nullable=False, unique=True),
    Column("event_type", String, nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("workflow_id", String, nullable=False),
    Column("execution_id", String, nullable=True),
    Column("node_id", String, nullable=True),
    Column("workflow_version", Integer, nullable=True),
    Column("correlation_id", String, nullable=True),
    Column("payload", JSONB_VARIANT, nullable=False),
    Index("ix_events_execution_position", "execution_id", "position"),
    Index("ix_events_workflow_position", "workflow_id", "position"),
)

# Node log lines (frontend Phase 15): what node code wrote through
# ``context.logger``, redacted and bounded per attempt by the runtime.
# Deleted with their execution.
node_logs = Table(
    "node_logs",
    metadata,
    # Store-assigned order, as for events: one CAS-guarded writer per
    # execution inserts its lines in order.
    Column("position", BigInteger, Identity(always=True), primary_key=True),
    Column(
        "execution_id",
        String,
        ForeignKey("executions.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("node_id", String, nullable=False),
    Column("attempt", Integer, nullable=False),
    Column("level", String, nullable=False),
    Column("message", String, nullable=False),
    Column("logged_at", DateTime(timezone=True), nullable=False),
    Column("fields", JSONB_VARIANT, nullable=False),
    CheckConstraint("attempt >= 1", name="ck_node_logs_attempt"),
    CheckConstraint(
        "level IN ('DEBUG','INFO','WARNING','ERROR','CRITICAL')",
        name="ck_node_logs_level",
    ),
    Index("ix_node_logs_execution_position", "execution_id", "position"),
)

_CREDENTIAL_KINDS = "'api_key','smtp','provider','generic'"

# Credential Store (Phase 13). ``ciphertext`` is AES-GCM output bound to the
# name; the plaintext never reaches the database.
credentials = Table(
    "credentials",
    metadata,
    Column("name", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("description", String, nullable=False, server_default=""),
    Column("ciphertext", LargeBinary, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(f"kind IN ({_CREDENTIAL_KINDS})", name="ck_credentials_kind"),
)
