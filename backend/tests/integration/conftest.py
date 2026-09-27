"""PostgreSQL integration fixtures (Testcontainers, backend/AGENTS.md §53).

One container per test session, one session-scoped event loop (configured in
pyproject.toml). The migrated schema is shared; every test starts on empty
tables via TRUNCATE — tests never depend on each other's rows or on any
developer-local PostgreSQL.
"""

import os
import shutil
import socket
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import pytest
from sqlalchemy import text
from testcontainers.community.postgres import PostgresContainer

from akasha.infrastructure.database import Database, normalize_async_url
from akasha.infrastructure.persistence import SqlAlchemyUnitOfWork

_POSTGRES_IMAGE = "postgres:17.2"

UowFactory = Callable[[], AbstractAsyncContextManager[SqlAlchemyUnitOfWork]]


def _docker_available() -> bool:
    """A missing daemon must skip, not hang (backend/AGENTS.md §117: keep the
    real integration test, report the infrastructure limitation)."""
    host = os.environ.get("DOCKER_HOST", "")
    sock = host[len("unix://") :] if host.startswith("unix://") else None
    if sock is None:
        if shutil.which("docker") is None:
            return False
        for candidate in ("/var/run/docker.sock", "~/.docker/run/docker.sock"):
            candidate = os.path.expanduser(candidate)
            if os.path.exists(candidate):
                sock = candidate
                break
        else:
            return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            s.connect(sock)
        return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def pg_container() -> Iterator[PostgresContainer]:
    if not _docker_available():
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
        for table in ("project_versions", "projects"):
            await conn.execute(text(f"TRUNCATE {table} CASCADE"))


@pytest.fixture
def uow(database: Database) -> UowFactory:
    """Unit-of-work factory; each call creates a fresh session/transaction."""

    @asynccontextmanager
    async def make() -> AsyncIterator[SqlAlchemyUnitOfWork]:
        async with SqlAlchemyUnitOfWork(database) as uow_ctx:
            yield uow_ctx

    return make
