"""PostgreSQL integration fixtures (Testcontainers, backend/AGENTS.md §53).

One container per test session, one session-scoped event loop (configured in
pyproject.toml). The migrated schema is shared; every test starts on empty
tables via TRUNCATE — tests never depend on each other's rows or on any
developer-local PostgreSQL.
"""

from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from akasha.application.auth import (
    AuthenticationService,
    AuthorizationService,
    UserService,
)
from akasha.application.sharing import SharingService
from akasha.application.workflows import WorkflowService
from akasha.domain.identity import Role
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
from akasha.security.passwords import PasswordHasher
from tests.support import docker_available

_POSTGRES_IMAGE = "postgres:17.2"
_REDIS_IMAGE = "redis:8.2.1-alpine"

UowFactory = Callable[[], AbstractAsyncContextManager[SqlAlchemyUnitOfWork]]


@pytest.fixture(scope="session")
def pg_container() -> Iterator[PostgresContainer]:
    if not docker_available():
        pytest.skip("Docker daemon not available for Testcontainers")
    with PostgresContainer(_POSTGRES_IMAGE) as container:
        yield container


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    """One Redis per session; tests isolate by key prefix, never by FLUSH."""
    if not docker_available():
        pytest.skip("Docker daemon not available for Testcontainers")
    container = (
        DockerContainer(_REDIS_IMAGE)
        .with_exposed_ports(6379)
        .waiting_for(LogMessageWaitStrategy("Ready to accept connections"))
    )
    with container:
        host = container.get_container_host_ip()
        yield f"redis://{host}:{container.get_exposed_port(6379)}/0"


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


async def truncate_all(database: Database) -> None:
    """Empty every table: a fresh installation on the migrated schema."""
    async with database.engine.begin() as conn:
        for table in (
            "events",
            "node_executions",
            "executions",
            "workflow_versions",
            "workflows",
            "credentials",
            "sessions",
            "users",
        ):
            await conn.execute(text(f"TRUNCATE {table} CASCADE"))


@pytest.fixture
async def clean_db(database: Database) -> None:
    """Guaranteed-empty tables per test; no inter-test state leakage (§54)."""
    await truncate_all(database)


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
def workflow_service(
    database: Database, node_definitions: dict[str, NodeDefinition]
) -> WorkflowService:
    return WorkflowService(lambda: SqlAlchemyUnitOfWork(database), node_definitions)


# Cheap scrypt parameters: tests exercise the hashing logic, not its cost
# (the production defaults are asserted in tests/test_passwords.py).
FAST_HASHER = PasswordHasher(n=2**4, r=8, p=1)
PASSWORD = "correct horse battery"


@dataclass
class AuthKit:
    """The Phase 20 services over the test database, plus shortcuts to get
    a logged-in client header."""

    auth: AuthenticationService
    authorization: AuthorizationService
    users: UserService
    sharing: SharingService

    def container_fields(self) -> dict[str, Any]:
        return {
            "auth": self.auth,
            "authorization": self.authorization,
            "users": self.users,
            "sharing": self.sharing,
        }

    async def admin_headers(self, username: str = "admin") -> dict[str, str]:
        """The installation's first admin (setup), logged in."""
        await self.auth.setup(username, PASSWORD)
        return await self.login(username)

    async def member_headers(
        self, admin: dict[str, str], username: str, role: Role = Role.MEMBER
    ) -> dict[str, str]:
        identity = await self.auth.authenticate(admin["Authorization"][7:])
        await self.users.create_user(identity, username, PASSWORD, role=role)
        return await self.login(username)

    async def login(self, username: str) -> dict[str, str]:
        result = await self.auth.login(username, PASSWORD)
        return {"Authorization": f"Bearer {result.token}"}


def make_auth_kit(database: Database, **auth_options: Any) -> AuthKit:
    def uow() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(database)

    options: dict[str, Any] = {
        "session_ttl": timedelta(hours=1),
        "hasher": FAST_HASHER,
        **auth_options,
    }
    auth = AuthenticationService(uow, **options)
    authorization = AuthorizationService(uow)
    return AuthKit(
        auth,
        authorization,
        UserService(uow, auth),
        SharingService(uow, authorization),
    )


@pytest.fixture
def auth_kit(database: Database) -> AuthKit:
    return make_auth_kit(database)
