"""PostgreSQL integration fixtures (Testcontainers, backend/AGENTS.md §53).

One container per test session, one session-scoped event loop (configured in
pyproject.toml). The migrated schema is shared; every test starts on empty
tables via TRUNCATE — tests never depend on each other's rows or on any
developer-local PostgreSQL.
"""

from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import pytest
from sqlalchemy import text
from testcontainers.community.postgres import PostgresContainer

from akasha.application.projects import ProjectService
from akasha.domain.node import (
    NodeConfigSchema,
    NodeDefinition,
    NodeInput,
    NodeMetadata,
    NodeOutput,
    PortType,
)
from akasha.infrastructure.database import Database, normalize_async_url
from akasha.infrastructure.persistence import SqlAlchemyUnitOfWork
from tests.support import docker_available

_POSTGRES_IMAGE = "postgres:17.2"

UowFactory = Callable[[], AbstractAsyncContextManager[SqlAlchemyUnitOfWork]]


@pytest.fixture(scope="session")
def pg_container() -> Iterator[PostgresContainer]:
    if not docker_available():
        pytest.skip("Docker daemon not available for Testcontainers")
    with PostgresContainer(_POSTGRES_IMAGE) as container:
        yield container


@pytest.fixture(scope="session")
async def db_url(pg_container: PostgresContainer) -> str:
    return (
        f"postgresql://{pg_container.username}:{pg_container.password}"
        f"@{pg_container.get_container_host_ip()}:"
        f"{pg_container.get_exposed_port(5432)}/{pg_container.dbname}"
    )


@pytest.fixture(scope="session")
async def database(db_url: str) -> AsyncIterator[Database]:
    db = Database(normalize_async_url(db_url))
    await db.run_migrations(db_url)
    try:
        yield db
    finally:
        await db.dispose()


@pytest.fixture
async def clean_db(database: Database) -> None:
    """Guaranteed-empty tables per test; no inter-test state leakage (§54)."""
    async with database.engine.begin() as conn:
        for table in (
            "events",
            "node_executions",
            "executions",
            "project_versions",
            "projects",
        ):
            await conn.execute(text(f"TRUNCATE {table} CASCADE"))


@pytest.fixture
def uow(database: Database) -> UowFactory:
    """Unit-of-work factory; each call creates a fresh session/transaction."""

    @asynccontextmanager
    async def make() -> AsyncIterator[SqlAlchemyUnitOfWork]:
        async with SqlAlchemyUnitOfWork(database) as uow_ctx:
            yield uow_ctx

    return make


# Two tiny node types: enough to build valid and invalid graphs. The real
# built-in registry is not needed here; the service takes any mapping.
SOURCE = NodeDefinition(
    metadata=NodeMetadata(
        node_id="test.source", name="Source", description="Emits text.", version="1.0.0"
    ),
    outputs=(NodeOutput(name="text", port_type=PortType.STRING),),
)
SINK = NodeDefinition(
    metadata=NodeMetadata(
        node_id="test.sink", name="Sink", description="Consumes text.", version="1.0.0"
    ),
    inputs=(NodeInput(name="text", port_type=PortType.STRING),),
    config_schema=NodeConfigSchema(
        {
            "type": "object",
            "properties": {"prefix": {"type": "string"}},
            "additionalProperties": False,
        }
    ),
)


@pytest.fixture
def node_definitions() -> dict[str, NodeDefinition]:
    return {d.node_id: d for d in (SOURCE, SINK)}


@pytest.fixture
def project_service(
    database: Database, node_definitions: dict[str, NodeDefinition]
) -> ProjectService:
    return ProjectService(lambda: SqlAlchemyUnitOfWork(database), node_definitions)
