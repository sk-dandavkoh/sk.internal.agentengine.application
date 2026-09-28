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
from akasha.nodes.builtins import create_builtin_registry
from akasha.nodes.registry import NodeRegistry
from akasha.security.network import NetworkPolicy

# A readiness check returns None when healthy, or a human-readable reason.
ReadyCheck = Callable[[], Awaitable[str | None]]


@dataclass
class Container:
    """Composition root. Application services get a UnitOfWork factory —
    one UnitOfWork per use case / transaction boundary."""

    settings: Settings
    nodes: NodeRegistry = field(default_factory=NodeRegistry)
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
    nodes = create_node_registry(settings)
    if not settings.database_url:
        return Container(settings=settings, nodes=nodes)
    database = Database(normalize_async_url(settings.database_url))
    # The same registry must later back the ExecutionRuntime (Phase 21/22
    # wiring), so publish-time and run-time validation agree.
    projects = ProjectService(lambda: SqlAlchemyUnitOfWork(database), nodes.definitions)
    return Container(
        settings=settings,
        nodes=nodes,
        database=database,
        projects=projects,
        ready_checks={"database": database.check},
    )


def create_node_registry(settings: Settings) -> NodeRegistry:
    """Built-in nodes configured from settings (plugins join in Phase 17)."""
    return create_builtin_registry(
        network=NetworkPolicy(
            allow_private_networks=settings.network_allow_private,
            allowed_hosts=frozenset(settings.network_allowed_hosts),
        ),
        web_search_url=None
        if settings.web_search_url is None
        else str(settings.web_search_url),
        http_max_response_bytes=settings.http_max_response_bytes,
    )
