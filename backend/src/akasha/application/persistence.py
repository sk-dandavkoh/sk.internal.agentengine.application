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

from collections.abc import Sequence
from datetime import datetime, timedelta
from types import TracebackType
from typing import Protocol

from akasha.domain.credentials import Credential
from akasha.domain.event import Event
from akasha.domain.execution import Execution
from akasha.domain.identity import User, UserSession
from akasha.domain.project import Project, ProjectVersion
from akasha.errors import AkashaError


class ConcurrentModificationError(AkashaError):
    """The persisted state changed since it was read (optimistic locking)."""

    code = "CONCURRENT_MODIFICATION"
    http_status = 409


class ProjectRepository(Protocol):
    """CRUD for the long-lived Project identity plus its mutable draft."""

    async def get(self, project_id: str) -> Project | None: ...

    async def save(self, project: Project) -> None:
        """Compare-and-swap on ``project.revision``.

        Revision 0 inserts; any other revision updates only if the stored
        revision still equals it. On success ``project.revision`` advances.
        Raises ``ConcurrentModificationError`` when the stored row moved on,
        was deleted, or (on insert) already exists.
        """
        ...

    async def delete(self, project_id: str, *, revision: int) -> bool:
        """Delete only if the stored revision still equals ``revision``;
        False when the project is missing or has moved on."""
        ...

    async def list(
        self, *, offset: int = 0, limit: int = 100, owner_id: str | None = None
    ) -> Sequence[Project]:
        """All projects, or only those owned by ``owner_id``."""
        ...

    async def using_plugin(
        self, plugin_id: str, node_types: Sequence[str]
    ) -> Sequence[str]:
        """Ids (sorted) of projects whose draft or any published version uses
        one of ``node_types`` or declares a dependency on plugin
        ``plugin_id``."""
        ...


class ProjectVersionRepository(Protocol):
    """Append-only access to immutable published versions.

    There is deliberately no ``update`` and no ``delete``: a published
    version is immutable (architecture §3.7) and retained for execution
    history and reproducibility.
    """

    async def get(self, project_id: str, version: int) -> ProjectVersion | None: ...
    async def save(self, project_id: str, version: ProjectVersion) -> None: ...
    async def list(
        self, project_id: str, *, offset: int = 0, limit: int = 100
    ) -> Sequence[ProjectVersion]: ...

    async def list_latest(
        self, *, project_id: str | None = None
    ) -> Sequence[tuple[str, ProjectVersion]]:
        """The highest published version of every project, or of one.

        Triggers always resolve against the latest published version
        (architecture §125): publishing retires the previous version's
        triggers. Returns (project_id, version) pairs ordered by project_id,
        so the result is deterministic. The pairs are needed because the
        domain object itself carries no project_id.
        """
        ...


class ExecutionRepository(Protocol):
    """Execution aggregate (execution row + its node executions).

    ``save`` is compare-and-swap on ``execution.revision`` exactly like
    ``ProjectRepository.save``: the runtime owning an execution is its only
    writer, and any other writer (a second runtime, an out-of-process cancel)
    makes the loser fail with ``ConcurrentModificationError`` instead of
    silently interleaving state.
    """

    async def get(self, execution_id: str) -> Execution | None: ...
    async def save(self, execution: Execution) -> None: ...
    async def list_queued(
        self, *, created_before: datetime, limit: int
    ) -> Sequence[str]:
        """Ids of QUEUED executions created before ``created_before``,
        oldest first."""
        ...

    async def count_unfinished(self, project_id: str) -> int:
        """Non-terminal executions of the project. Locks the project row
        for the transaction, so concurrent admissions to one project
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
        """Ids of started (running/paused/waiting) executions without a
        heartbeat for ``stale_after``, stalest first."""
        ...

    async def claim_stale(self, execution_id: str, *, stale_after: timedelta) -> bool:
        """Lock the execution row for this transaction if it is still stale.
        Skips a row another recoverer holds; an owner's heartbeat then waits
        for the commit and finds the revision moved on."""
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
        project_id: str | None = None,
        after: int = 0,
        limit: int = 100,
    ) -> Sequence[Event]:
        """Stored events with ``position > after``, ascending by position.

        Ordering is guaranteed per execution and per project (each has a
        single CAS-guarded writer). Across unrelated streams positions only
        approximate commit order, so a global "after" cursor may skip a
        concurrently committed event.
        """
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
        """Update ``disabled`` (and the hash when given); False if missing."""
        ...

    async def lock_active_admins(self) -> int:
        """Lock the active admin rows until the transaction ends and count
        them, so two admins disabling each other cannot both succeed."""
        ...

    async def list(self, *, offset: int = 0, limit: int = 100) -> Sequence[User]: ...


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


class UnitOfWork(Protocol):
    """Explicit transaction boundary (backend/AGENTS.md §16).

    Repositories bound to the uow participate in its transaction. Commit is
    the only success path; leaving the context without commit rolls back.
    """

    projects: ProjectRepository
    versions: ProjectVersionRepository
    executions: ExecutionRepository
    events: EventRepository
    credentials: CredentialRepository
    users: UserRepository
    sessions: SessionRepository

    async def __aenter__(self) -> "UnitOfWork": ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...
