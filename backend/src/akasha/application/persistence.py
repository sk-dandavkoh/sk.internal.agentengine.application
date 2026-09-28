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


class UnitOfWork(Protocol):
    """Explicit transaction boundary (backend/AGENTS.md §16).

    Repositories bound to the uow participate in its transaction. Commit is
    the only success path; leaving the context without commit rolls back.
    """

    projects: ProjectRepository
    versions: ProjectVersionRepository

    async def __aenter__(self) -> "UnitOfWork": ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...
