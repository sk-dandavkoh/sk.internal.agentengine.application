"""Persistence interfaces owned by the application layer (architecture §25).

Application services and later the runtime depend on these protocols; the
SQLAlchemy implementations live in ``akasha.infrastructure`` (dependency
rule: infrastructure → application interfaces, never the reverse).

Boundary rules (backend/AGENTS.md §13/§15/§16):
- repositories receive a caller-provided session; they never begin, commit
  or roll back transactions themselves
- transaction boundaries are exclusively the ``UnitOfWork``
- domain objects in, domain objects out — SQLAlchemy rows never cross
  this interface
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from types import TracebackType
from typing import Any, Protocol

from akasha.domain.credentials import Credential
from akasha.domain.event import Event
from akasha.domain.execution import (
    Execution,
    ExecutionMetadata,
    ExecutionStatus,
    LogLevel,
    NodeExecutionStatus,
    NodeLogEntry,
)
from akasha.domain.identity import (
    AccessToken,
    Role,
    User,
    UserSession,
    WorkflowMember,
    WorkflowRole,
)
from akasha.domain.workflow import Workflow, WorkflowStatus, WorkflowVersion
from akasha.errors import AkashaError


class ConcurrentModificationError(AkashaError):
    """The persisted state changed since it was read (optimistic locking)."""

    code = "CONCURRENT_MODIFICATION"
    http_status = 409


class WorkflowSort(StrEnum):
    """Workflow list orders; a leading ``-`` means descending. Every order
    breaks ties by id, so pages are stable."""

    CREATED = "created_at"
    CREATED_DESC = "-created_at"
    UPDATED = "updated_at"
    UPDATED_DESC = "-updated_at"
    NAME = "name"
    NAME_DESC = "-name"


class WorkflowRepository(Protocol):
    """CRUD for the long-lived Workflow identity plus its mutable draft."""

    async def get(self, workflow_id: str) -> Workflow | None: ...

    async def save(self, workflow: Workflow) -> None:
        """Compare-and-swap on ``workflow.revision``.

        Revision 0 inserts; any other revision updates only if the stored
        revision still equals it. On success ``workflow.revision`` advances.
        Raises ``ConcurrentModificationError`` when the stored row moved on,
        was deleted, or (on insert) already exists.
        """
        ...

    async def delete(self, workflow_id: str, *, revision: int) -> bool:
        """Delete only if the stored revision still equals ``revision``;
        False when the workflow is missing or has moved on."""
        ...

    async def list(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
        visible_to: str | None = None,
        search: str | None = None,
        statuses: Collection[WorkflowStatus] = (),
        sort: WorkflowSort = WorkflowSort.CREATED,
    ) -> Sequence[Workflow]:
        """All workflows, or only those ``visible_to`` that user (owned or
        shared with them); optionally
        only names containing ``search`` (case-insensitive, literal) and
        only ``statuses``."""
        ...

    async def lock_owner(self, workflow_id: str) -> str | None:
        """The owner of an existing workflow, with its row locked until the
        transaction ends."""
        ...

    async def set_owner(self, workflow_id: str, owner_id: str) -> None:
        """Change the owner; not a draft change, so the revision stays."""
        ...

    async def using_plugin(
        self, plugin_id: str, node_types: Sequence[str]
    ) -> Sequence[str]:
        """Ids (sorted) of workflows whose draft or any published version uses
        one of ``node_types`` or declares a dependency on plugin
        ``plugin_id``."""
        ...


class WorkflowVersionRepository(Protocol):
    """Append-only access to immutable published versions.

    There is deliberately no ``update`` and no ``delete``: a published
    version is immutable (architecture §3.7) and retained for execution
    history and reproducibility.
    """

    async def get(self, workflow_id: str, version: int) -> WorkflowVersion | None: ...
    async def save(self, workflow_id: str, version: WorkflowVersion) -> None: ...
    async def list(
        self, workflow_id: str, *, offset: int = 0, limit: int = 100
    ) -> Sequence[WorkflowVersion]: ...

    async def list_latest(
        self, *, workflow_id: str | None = None
    ) -> Sequence[tuple[str, WorkflowVersion]]:
        """The highest published version of every workflow, or of one.

        Triggers always resolve against the latest published version
        (architecture §125): publishing retires the previous version's
        triggers. Returns (workflow_id, version) pairs ordered by workflow_id,
        so the result is deterministic. The pairs are needed because the
        domain object itself carries no workflow_id.
        """
        ...


@dataclass(frozen=True)
class ExecutionSummary:
    """Read model for listings: the execution row without its node
    executions or version snapshot, so a page costs one query."""

    id: str
    workflow_id: str
    # Joined from the workflow row so a listing names its workflows without
    # a request per workflow.
    workflow_name: str
    workflow_version: int
    status: ExecutionStatus
    metadata: ExecutionMetadata
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class ExecutionSample:
    """The execution columns monitoring needs; read model, not the aggregate."""

    status: ExecutionStatus
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    error_kind: str | None


@dataclass(frozen=True, slots=True)
class NodeSample:
    node_type: str
    status: NodeExecutionStatus
    attempts: int
    failed_attempts: int
    started_at: datetime | None
    finished_at: datetime | None
    # ``outputs["metadata"]``, where AI nodes report usage and cost.
    metadata: Any


class ExecutionRepository(Protocol):
    """Execution aggregate (execution row + its node executions).

    ``save`` is compare-and-swap on ``execution.revision`` exactly like
    ``WorkflowRepository.save``: the runtime owning an execution is its only
    writer, and any other writer (a second runtime, an out-of-process cancel)
    makes the loser fail with ``ConcurrentModificationError`` instead of
    silently interleaving state.
    """

    async def get(self, execution_id: str) -> Execution | None: ...
    async def save(self, execution: Execution) -> None: ...
    async def list_summaries(
        self,
        *,
        workflow_id: str | None = None,
        workflow_version: int | None = None,
        visible_to: str | None = None,
        statuses: Collection[ExecutionStatus] = (),
        offset: int = 0,
        limit: int = 100,
    ) -> Sequence[ExecutionSummary]:
        """Executions newest first: of one workflow or all, limited to the
        workflows ``visible_to`` that user, to one ``workflow_version`` (meaningful
        with ``workflow_id``) and to ``statuses`` when given."""
        ...

    async def count_by_status(
        self, *, visible_to: str | None = None
    ) -> Mapping[ExecutionStatus, int]:
        """Executions per status (absent statuses: none), limited to the
        workflows ``visible_to`` that user when given."""
        ...

    # Monitoring read models (Phase 16): executions created in
    # [since, until), limited to the workflows ``visible_to`` that user.
    async def sample_executions(
        self, *, since: datetime, until: datetime, visible_to: str | None = None
    ) -> Sequence[ExecutionSample]: ...

    async def sample_node_executions(
        self, *, since: datetime, until: datetime, visible_to: str | None = None
    ) -> Sequence[NodeSample]:
        """Node executions of those executions, by execution id and node id
        (a stable order for float sums)."""
        ...

    async def list_queued(
        self, *, created_before: datetime, limit: int
    ) -> Sequence[str]:
        """Ids of QUEUED executions created before ``created_before``,
        oldest first."""
        ...

    async def count_unfinished(self, workflow_id: str) -> int:
        """Non-terminal executions of the workflow. Locks the workflow row
        for the transaction, so concurrent admissions to one workflow
        serialize and cannot both pass a capacity check (Phase 24)."""
        ...

    # Owner liveness (Phase 23). Every ``save`` also refreshes the
    # heartbeat. Times come from the database clock, one clock for all
    # hosts, so stale detection does not depend on their clocks agreeing.
    async def heartbeat(self, execution_id: str, *, revision: int) -> bool:
        """Refresh the owner's heartbeat if the row is still at ``revision``
        (nobody else wrote it). False: the caller no longer owns the run."""
        ...

    async def list_stale(self, *, stale_after: timedelta, limit: int) -> Sequence[str]:
        """Ids of running/paused executions without a heartbeat for
        ``stale_after``, stalest first. WAITING executions have no owner, so
        they are never stale (``list_due_waits`` covers their deadlines)."""
        ...

    async def claim_stale(self, execution_id: str, *, stale_after: timedelta) -> bool:
        """Lock the execution row for this transaction if it is still stale.
        Skips a row another recoverer holds; an owner's heartbeat then waits
        for the commit and finds the revision moved on."""
        ...

    async def list_due_waits(self, *, now: datetime, limit: int) -> Sequence[str]:
        """Ids of WAITING executions whose ``wake_at`` is at or before
        ``now``, earliest first."""
        ...

    async def claim_due_wait(self, execution_id: str, *, now: datetime) -> bool:
        """Lock the execution row for this transaction if it is still a due
        WAITING execution; skips a row another worker holds."""
        ...


class EventRepository(Protocol):
    """Append-only event store (no update, no delete: events are history).

    ``append`` participates in the caller's transaction, so events commit
    atomically with the state change they describe — never one without the
    other. It is idempotent on ``event_id``: re-appending a stored event is a
    no-op, so a redelivered or retried append cannot duplicate history.
    """

    async def append(self, events: Sequence[Event]) -> None: ...

    async def list(
        self,
        *,
        execution_id: str | None = None,
        workflow_id: str | None = None,
        after: int = 0,
        limit: int = 100,
        positions: Collection[int] | None = None,
    ) -> Sequence[Event]:
        """Stored events with ``position > after`` (and among ``positions``
        when given), ascending by position.

        Ordering is guaranteed per execution and per workflow (each has a
        single CAS-guarded writer). Across unrelated streams positions only
        approximate commit order, so a global "after" cursor may skip a
        concurrently committed event; ``positions`` lets such a reader
        re-check the gaps it saw.
        """
        ...

    async def max_position(self) -> int:
        """The highest stored position; 0 when there are no events."""
        ...


class NodeLogRepository(Protocol):
    """Append-only node log lines; ``append`` joins the caller's transaction
    (stored with the attempt outcome they belong to)."""

    async def append(self, entries: Sequence[NodeLogEntry]) -> None: ...

    async def list(
        self,
        execution_id: str,
        *,
        after: int = 0,
        limit: int = 100,
        node_id: str | None = None,
        levels: Collection[LogLevel] = (),
        contains: str | None = None,
    ) -> Sequence[NodeLogEntry]:
        """Lines with ``position > after`` ascending, optionally of one node,
        of these levels (all when empty) and whose message contains
        ``contains`` (case-insensitive, literally: no wildcards)."""
        ...


class CredentialRepository(Protocol):
    """Credential metadata plus the *encrypted* secret. Plaintext never
    crosses this interface: encryption happens in the application layer."""

    async def get(self, name: str) -> Credential | None: ...
    async def get_ciphertext(self, name: str) -> bytes | None: ...

    async def add(self, credential: Credential, ciphertext: bytes) -> bool:
        """Insert; False when the name is already taken."""
        ...

    async def update(self, credential: Credential, ciphertext: bytes | None) -> bool:
        """Update metadata, and the secret when ``ciphertext`` is given;
        False when the credential does not exist."""
        ...

    async def delete(self, name: str) -> bool: ...
    async def list(
        self, *, offset: int = 0, limit: int = 100
    ) -> Sequence[Credential]: ...


class UserRepository(Protocol):
    """Local accounts. The password hash crosses this interface only as an
    opaque string; hashing happens in the application layer."""

    async def get(self, user_id: str) -> User | None: ...

    async def get_by_username(self, username: str) -> User | None: ...

    async def get_with_password(self, username: str) -> tuple[User, str] | None:
        """The user and their stored password hash, for login only."""
        ...

    async def add(self, user: User, password_hash: str) -> bool:
        """Insert; False when the username is taken."""
        ...

    async def add_first(self, user: User, password_hash: str) -> bool:
        """Insert only into an empty user table; False otherwise. Safe
        against concurrent callers: at most one first user can exist."""
        ...

    async def update(self, user: User, password_hash: str | None = None) -> bool:
        """Update role, display name and ``disabled`` (and the hash when
        given); False if missing. The username is immutable."""
        ...

    async def record_login(self, user_id: str, at: datetime) -> None: ...

    async def lock_active_admins(self) -> int:
        """Lock the active admin rows until the transaction ends and count
        them, so two admins disabling each other cannot both succeed."""
        ...

    async def list(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
        search: str | None = None,
        role: Role | None = None,
        disabled: bool | None = None,
    ) -> Sequence[User]:
        """By username; ``search`` matches username or display name (any
        case, a literal substring)."""
        ...


class SessionRepository(Protocol):
    """Server-side login sessions, keyed by the hash of their token."""

    async def add(self, session: UserSession) -> None: ...

    async def get_active(
        self, token_hash: str, now: datetime
    ) -> tuple[UserSession, User] | None:
        """The unexpired session and its user, only if the user is enabled."""
        ...

    async def delete(self, token_hash: str) -> None: ...

    async def delete_for_user(self, user_id: str, *, keep: str | None = None) -> None:
        """Revoke every session of ``user_id`` except the ``keep`` hash."""
        ...

    async def delete_expired(self, now: datetime) -> None: ...


class AccessTokenRepository(Protocol):
    """Personal access tokens, looked up by the hash of their value."""

    async def add(self, token: AccessToken) -> None: ...

    async def get_active(
        self, token_hash: str, now: datetime
    ) -> tuple[AccessToken, User] | None:
        """The unexpired token and its user, only if the user is enabled."""
        ...

    async def list_for_user(self, user_id: str) -> Sequence[AccessToken]:
        """Newest first."""
        ...

    async def delete(self, token_id: str, *, user_id: str) -> bool:
        """Revoke one of ``user_id``'s tokens; False if it has none such."""
        ...

    async def delete_for_user(self, user_id: str) -> None: ...

    async def mark_used(
        self, token_id: str, now: datetime, *, every: timedelta
    ) -> None:
        """Record use, at most once per ``every``: a token authenticates
        every request, so recording each use would write on every read."""
        ...


class WorkflowMemberRepository(Protocol):
    """Who a workflow is shared with (the owner is not a member row)."""

    async def role(self, workflow_id: str, user_id: str) -> WorkflowRole | None: ...

    async def list(self, workflow_id: str) -> Sequence[WorkflowMember]:
        """By username."""
        ...

    async def count(self, workflow_id: str) -> int: ...

    async def put(
        self, workflow_id: str, user_id: str, role: WorkflowRole, at: datetime
    ) -> None:
        """Add the member, or change their role (keeping ``created_at``)."""
        ...

    async def delete(self, workflow_id: str, user_id: str) -> bool:
        """False when the user was not a member."""
        ...


class UnitOfWork(Protocol):
    """Explicit transaction boundary (backend/AGENTS.md §16).

    Repositories bound to the uow participate in its transaction. Commit is
    the only success path; leaving the context without commit rolls back.
    """

    workflows: WorkflowRepository
    versions: WorkflowVersionRepository
    executions: ExecutionRepository
    events: EventRepository
    node_logs: NodeLogRepository
    credentials: CredentialRepository
    users: UserRepository
    sessions: SessionRepository
    access_tokens: AccessTokenRepository
    members: WorkflowMemberRepository

    async def __aenter__(self) -> "UnitOfWork": ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...
