"""Shared fixtures: a test settings object and an app built from it."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from akasha.api.app import create_app
from akasha.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(environment="test", log_level="CRITICAL")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings=settings)
    with TestClient(app) as client:
        yield client
