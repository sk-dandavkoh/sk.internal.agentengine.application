"""Baseline tests: package imports and the app factory builds a serving app."""

from fastapi.testclient import TestClient

import akasha
from akasha.api.app import create_app
from akasha.main import main


def test_version_is_defined() -> None:
    assert akasha.__version__


def test_entry_point_exists() -> None:
    # main() itself blocks on uvicorn; the HTTP behavior is covered by
    # test_health.py / test_lifecycle.py via the app factory.
    assert callable(main)


def test_app_factory_serves_endpoints() -> None:
    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 200
