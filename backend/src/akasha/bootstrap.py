"""Application composition: single, explicit build point for dependencies.

The Container is created once at startup and stored on ``app.state``. No
global singletons — tests build their own Container and FastAPI app via
``create_container`` / ``create_app``.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from akasha.config import Settings
from akasha.infrastructure.database import Database, normalize_async_url

# A readiness check returns None when healthy, or a human-readable reason.
ReadyCheck = Callable[[], Awaitable[str | None]]


@dataclass
class Container:
    """Composition root. Repositories per Phase 05 are constructed from
    ``database`` at the use-site (one UnitOfWork per transaction boundary)."""

    settings: Settings
    database: Database | None = None
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
    engine still boots (persistence consumers fail at use), but readiness
    has nothing to check. Tests inject their own container.
    """
    database: Database | None = None
    ready_checks: dict[str, ReadyCheck] = {}
    if settings.database_url:
        database = Database(normalize_async_url(settings.database_url))
        ready_checks["database"] = database.check
    return Container(settings=settings, database=database, ready_checks=ready_checks)
