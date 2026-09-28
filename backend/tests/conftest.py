"""Shared fixtures: a test settings object, an app built from it, and a
local HTTP server for network nodes."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from akasha.api.app import create_app
from akasha.config import Settings
from tests.support import (
    LocalHttpServer,
    Mailpit,
    docker_available,
    run_mailpit,
    serve_http,
)


@pytest.fixture
def settings() -> Settings:
    return Settings(environment="test", log_level="CRITICAL")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings=settings)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def http_server() -> Iterator[LocalHttpServer]:
    with serve_http() as server:
        yield server


@pytest.fixture(scope="session")
def mailpit() -> Iterator[Mailpit]:
    if not docker_available():
        pytest.skip("Docker daemon not available for Testcontainers")
    with run_mailpit() as server:
        yield server


@pytest.fixture
def mailbox(mailpit: Mailpit) -> Mailpit:
    """The shared Mailpit, emptied: every test sees only its own mail."""
    mailpit.clear()
    return mailpit
