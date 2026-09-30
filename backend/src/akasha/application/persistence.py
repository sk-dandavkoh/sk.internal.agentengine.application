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
from types import TracebackType
from typing import Protocol

from akasha.domain.event import Event
from akasha.domain.execution import Execution
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

    async def list(self, *, offset: int = 0, limit: int = 100) -> Sequence[Project]: ...


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


class UnitOfWork(Protocol):
    """Explicit transaction boundary (backend/AGENTS.md §16).

    Repositories bound to the uow participate in its transaction. Commit is
    the only success path; leaving the context without commit rolls back.
    """

    projects: ProjectRepository
    versions: ProjectVersionRepository
    executions: ExecutionRepository
    events: EventRepository

    async def __aenter__(self) -> "UnitOfWork": ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...
