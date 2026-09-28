"""Startup/shutdown lifecycle and dependency wiring."""

import logging

from _pytest.logging import LogCaptureFixture
from fastapi.testclient import TestClient

from akasha.api.app import create_app
from akasha.bootstrap import create_container
from akasha.config import Settings


def test_lifespan_logs_start_and_stop(caplog: LogCaptureFixture) -> None:
    settings = Settings(environment="test", log_level="INFO")
    app = create_app(settings=settings)  # configure_logging before caplog attaches
    with caplog.at_level(logging.INFO, logger="akasha"):
        with TestClient(app):
            pass
    events = [r.__dict__.get("event") for r in caplog.records]
    assert "application_started" in events
    assert "application_stopped" in events


def test_container_is_wired_into_app_state() -> None:
    settings = Settings(environment="test")
    container = create_container(settings)
    app = create_app(settings=settings, container=container)
    assert app.state.container is container
    assert container.projects is None  # no database configured


async def test_container_wires_project_service_with_database() -> None:
    # Engine creation is lazy: no connection is attempted here.
    settings = Settings(
        environment="test", database_url="postgresql://u:p@127.0.0.1:1/akasha"
    )
    container = create_container(settings)
    try:
        assert container.projects is not None
        assert set(container.ready_checks) == {"database"}
    finally:
        await container.shutdown()
