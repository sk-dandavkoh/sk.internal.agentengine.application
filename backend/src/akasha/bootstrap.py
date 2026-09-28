"""Application composition: single, explicit build point for dependencies.

The Container is created once at startup and stored on ``app.state``. No
global singletons — tests build their own Container and FastAPI app via
``create_container`` / ``create_app``.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from akasha.application.projects import ProjectService
from akasha.config import Settings
from akasha.infrastructure.database import Database, normalize_async_url
from akasha.infrastructure.persistence import SqlAlchemyUnitOfWork

# A readiness check returns None when healthy, or a human-readable reason.
ReadyCheck = Callable[[], Awaitable[str | None]]


@dataclass
class Container:
    """Composition root. Application services get a UnitOfWork factory —
    one UnitOfWork per use case / transaction boundary."""

    settings: Settings
    database: Database | None = None
    projects: ProjectService | None = None
    ready_checks: dict[str, ReadyCheck] = field(default_factory=dict)

    async def readiness_failures(self) -> dict[str, str]:
        """Run registered readiness checks; engine with no checks is ready."""
        failures: dict[str, str] = {}
        for name, check in self.ready_checks.items():
            reason = await check()
            if reason is not None:
                failures[name] = reason
        return failures

    async def shutdown(self) -> None:
        """Release resources held by the container (connections, pools)."""
        if self.database is not None:
            await self.database.dispose()


def create_container(settings: Settings) -> Container:
    """Wire infrastructure from configuration.

    A missing ``database_url`` yields a container *without* a database: the
    engine still boots, project endpoints answer ``CONFIGURATION_ERROR``,
    and readiness has nothing to check. Tests inject their own container.
    """
    if not settings.database_url:
        return Container(settings=settings)
    database = Database(normalize_async_url(settings.database_url))
    # ponytail: empty node catalog — every node type is unknown, so nothing
    # validates/publishes in production until the Node Registry (Phase 09)
    # supplies definitions here.
    projects = ProjectService(lambda: SqlAlchemyUnitOfWork(database), {})
    return Container(
        settings=settings,
        database=database,
        projects=projects,
        ready_checks={"database": database.check},
    )
