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
        assert container.triggers is not None
        assert container.trigger_registry is not None
        assert container.scheduler is not None
        assert set(container.ready_checks) == {"database", "schema"}
        # One shared publisher: subscribing to container.events reaches
        # every producer.
        assert container.executions is not None
        assert container.executions._events is container.events
        assert container.projects._events is container.events
    finally:
        await container.shutdown()


async def test_trigger_tasks_start_and_stop_with_container() -> None:
    # Long-poll keeps the task in sleep; no database connection is made
    # before cancel (a failed tick is caught and logged by the loop anyway).
    settings = Settings(
        environment="test",
        database_url="postgresql://u:p@127.0.0.1:1/akasha",
        scheduler_poll_seconds=60,
    )
    container = create_container(settings)
    await container.start()
    tasks = list(container._tasks)
    assert [t.get_name() for t in tasks] == [
        "trigger-scheduler",
        "event-trigger-dispatcher",
    ]
    assert not any(t.done() for t in tasks)
    await container.start()  # idempotent: no second pair of loops
    assert container._tasks == tasks
    await container.shutdown()
    assert all(t.done() for t in tasks)
    assert container._tasks == []


async def test_scheduler_disabled_by_configuration() -> None:
    settings = Settings(
        environment="test",
        database_url="postgresql://u:p@127.0.0.1:1/akasha",
        scheduler_enabled=False,
    )
    container = create_container(settings)
    try:
        assert container.scheduler is None
        assert container.event_triggers is None
        await container.start()  # no-op without a scheduler
        assert container._tasks == []
    finally:
        await container.shutdown()
