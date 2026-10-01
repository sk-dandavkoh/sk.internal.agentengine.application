"""Phase 10 end-to-end through the real HTTP stack and PostgreSQL.

App → router → TriggerRegistry/TriggerService → ExecutionService → Unit of
Work → PostgreSQL; nothing about trigger resolution or persistence mocked
(backend/AGENTS.md §56). Scheduler behaviour against the real database is
covered with an injected clock, never with sleeps (§62).
"""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from akasha.api.app import create_app
from akasha.application.executions import ExecutionService
from akasha.application.projects import ProjectService
from akasha.application.triggers import TriggerRegistry, TriggerService
from akasha.bootstrap import Container
from akasha.config import Settings
from akasha.domain.execution import ExecutionStatus
from akasha.domain.node import Node, NodeExecutionContext, NodeResult
from akasha.domain.primitives import new_id
from akasha.infrastructure.database import Database
from akasha.infrastructure.persistence import SqlAlchemyUnitOfWork
from akasha.runtime.plan import NodeCatalog
from akasha.runtime.runtime import ExecutionRuntime
from akasha.runtime.scheduler import TriggerScheduler
from tests.integration.conftest import SINK, SOURCE, make_auth_kit

pytestmark = pytest.mark.usefixtures("clean_db")

ERROR_KEYS = {"code", "message", "details", "request_id"}
UNKNOWN_ID = "trig_" + "0" * 32
UNKNOWN_PROJECT = "proj_" + "0" * 32


def invoke_url(project_id: str, trigger_id: str) -> str:
    return f"/api/v1/projects/{project_id}/triggers/{trigger_id}/invoke"


def webhook_url(project_id: str, trigger_id: str) -> str:
    return f"/api/v1/webhooks/{project_id}/{trigger_id}"


def uow_of(database: Database) -> SqlAlchemyUnitOfWork:
    return SqlAlchemyUnitOfWork(database)


@pytest.fixture
async def api(
    database: Database, project_service: ProjectService
) -> AsyncIterator[httpx.AsyncClient]:
    async with trigger_client(database, project_service) as client:
        yield client


@asynccontextmanager
async def trigger_client(
    database: Database,
    project_service: ProjectService,
    **setting_overrides: Any,
) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(environment="test", log_level="CRITICAL", **setting_overrides)
    auth_kit = make_auth_kit(database)
    executions = ExecutionService(
        lambda: uow_of(database), limits=settings.resource_limits()
    )
    container = Container(
        settings=settings,
        database=database,
        projects=project_service,
        executions=executions,
        triggers=TriggerService(executions),
        trigger_registry=TriggerRegistry(lambda: uow_of(database)),
        **auth_kit.container_fields(),
    )
    app = create_app(settings=settings, container=container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers=await auth_kit.admin_headers(),
    ) as client:
        yield client


def draft_with(triggers: list[dict[str, Any]]) -> dict[str, Any]:
    source, sink = new_id("node"), new_id("node")
    return {
        "graph": {
            "nodes": [
                {"id": source, "node_type": "test.source"},
                {"id": sink, "node_type": "test.sink", "config": {"prefix": ">"}},
            ],
            "edges": [
                {
                    "source_node_id": source,
                    "source_port": "text",
                    "target_node_id": sink,
                    "target_port": "text",
                }
            ],
        },
        "triggers": triggers,
        "dependencies": [],
    }


async def publish_with(
    api: httpx.AsyncClient,
    triggers: list[dict[str, Any]],
    *,
    project_id: str | None = None,
    revision: int = 1,
) -> tuple[str, int, int]:
    """Create (or replace) the draft and publish; returns
    (project_id, version, next_expected_revision)."""
    if project_id is None:
        project_id = (await api.post("/api/v1/projects", json={"name": "t"})).json()[
            "id"
        ]
    updated = await api.put(
        f"/api/v1/projects/{project_id}",
        json={
            "expected_revision": revision,
            "name": "t",
            "draft": draft_with(triggers),
        },
    )
    assert updated.status_code == 200, updated.text
    published = await api.post(
        f"/api/v1/projects/{project_id}/publish",
        json={"expected_revision": revision + 1},
    )
    assert published.status_code == 201, published.text
    return project_id, published.json()["version"], revision + 2


def assert_error(response: httpx.Response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == ERROR_KEYS
    assert body["code"] == code
    assert "Traceback" not in response.text


def assert_execution(
    body: dict[str, Any],
    *,
    project_id: str,
    trigger_type: str,
    trigger_id: str | None,
    correlation_id: str | None = None,
) -> None:
    assert set(body) >= {
        "id",
        "project_id",
        "version",
        "status",
        "trigger_type",
        "trigger_id",
    }
    assert body["project_id"] == project_id
    assert body["status"] == "queued"
    assert body["trigger_type"] == trigger_type
    assert body["trigger_id"] == trigger_id
    assert body["correlation_id"] == correlation_id


# -- manual execution (§126) -----------------------------------------------


async def test_manual_execute_creates_canonical_execution(
    api: httpx.AsyncClient,
) -> None:
    pid, version, _ = await publish_with(api, [])

    response = await api.post(
        f"/api/v1/projects/{pid}/versions/{version}/execute",
        json={"goal": "run"},
        headers={"x-request-id": "req-manual-1"},
    )

    assert response.status_code == 201, response.text
    assert_execution(
        response.json(),
        project_id=pid,
        trigger_type="manual",
        trigger_id=None,
        correlation_id="req-manual-1",
    )
    execution = (await api.get(f"/api/v1/executions/{response.json()['id']}")).json()
    assert execution["status"] == "queued"
    assert execution["version"] == version


async def test_manual_execute_unknown_version(api: httpx.AsyncClient) -> None:
    pid, _, _ = await publish_with(api, [])
    response = await api.post(f"/api/v1/projects/{pid}/versions/99/execute", json={})
    assert_error(response, 404, "PROJECT_VERSION_NOT_FOUND")


async def test_manual_execute_over_project_capacity_is_429(
    database: Database, project_service: ProjectService
) -> None:
    async with trigger_client(
        database, project_service, limit_active_executions_per_project=1
    ) as api:
        pid, version, _ = await publish_with(api, [])
        url = f"/api/v1/projects/{pid}/versions/{version}/execute"
        assert (await api.post(url, json={})).status_code == 201
        response = await api.post(url, json={})
    assert_error(response, 429, "EXECUTION_CAPACITY_EXCEEDED")
    assert response.json()["details"] == {
        "limit": "active_executions",
        "maximum": 1,
        "requested": 2,
    }


async def test_get_unknown_execution(api: httpx.AsyncClient) -> None:
    response = await api.get(f"/api/v1/executions/exec_{'0' * 32}")
    assert_error(response, 404, "EXECUTION_NOT_FOUND")


async def test_manual_execute_invalid_input_rejected(api: httpx.AsyncClient) -> None:
    pid, version, _ = await publish_with(api, [])
    response = await api.post(
        f"/api/v1/projects/{pid}/versions/{version}/execute", json=["not", "an-object"]
    )
    assert response.status_code == 422


# -- API trigger invocation --------------------------------------------------


async def test_invoke_api_trigger(api: httpx.AsyncClient) -> None:
    trigger_id = new_id("trig")
    pid, _, _ = await publish_with(api, [{"id": trigger_id, "trigger_type": "api"}])

    response = await api.post(invoke_url(pid, trigger_id), json={"ticket": 7})

    assert response.status_code == 201, response.text
    # No X-Request-ID sent: the minted request id is the correlation root.
    assert_execution(
        response.json(),
        project_id=pid,
        trigger_type="api",
        trigger_id=trigger_id,
        correlation_id=response.headers["x-request-id"],
    )


async def test_invoke_manual_trigger_allowed(api: httpx.AsyncClient) -> None:
    trigger_id = new_id("trig")
    pid, _, _ = await publish_with(api, [{"id": trigger_id, "trigger_type": "manual"}])

    response = await api.post(invoke_url(pid, trigger_id))

    assert response.status_code == 201, response.text
    # No X-Request-ID sent: the minted request id is the correlation root.
    assert_execution(
        response.json(),
        project_id=pid,
        trigger_type="manual",
        trigger_id=trigger_id,
        correlation_id=response.headers["x-request-id"],
    )


async def test_invoke_unknown_or_foreign_type_is_404(api: httpx.AsyncClient) -> None:
    trigger_id = new_id("trig")
    pid, _, _ = await publish_with(api, [{"id": trigger_id, "trigger_type": "webhook"}])

    # Unknown ids and a WEBHOOK trigger alike: the invoke endpoint must not
    # reveal which mechanisms an id answers to (§49). An unknown project is
    # stopped earlier, by the project access check.
    for url in (invoke_url(pid, UNKNOWN_ID), invoke_url(pid, trigger_id)):
        assert_error(await api.post(url), 404, "TRIGGER_NOT_FOUND")
    response = await api.post(invoke_url(UNKNOWN_PROJECT, trigger_id))
    assert_error(response, 404, "PROJECT_NOT_FOUND")


# -- webhook invocation (§127) -----------------------------------------------


async def test_webhook_fires_with_payload_as_input(
    api: httpx.AsyncClient, database: Database
) -> None:
    trigger_id = new_id("trig")
    pid, _, _ = await publish_with(api, [{"id": trigger_id, "trigger_type": "webhook"}])

    response = await api.post(
        webhook_url(pid, trigger_id),
        json={"order_id": "A-1"},
        headers={"authorization": "Bearer must-not-leak", "x-request-id": "wh-1"},
    )

    assert response.status_code == 202, response.text
    assert_execution(
        response.json(),
        project_id=pid,
        trigger_type="webhook",
        trigger_id=trigger_id,
        correlation_id="wh-1",
    )
    # The webhook body became the execution input, persisted.
    stored = await ExecutionService(lambda: uow_of(database)).get_execution(
        response.json()["id"]
    )
    assert stored.input == {"order_id": "A-1"}


async def test_webhook_unknown_or_wrong_type_is_404(api: httpx.AsyncClient) -> None:
    api_trigger_id = new_id("trig")
    pid, _, _ = await publish_with(api, [{"id": api_trigger_id, "trigger_type": "api"}])

    for url in (
        webhook_url(pid, UNKNOWN_ID),
        webhook_url(UNKNOWN_PROJECT, api_trigger_id),
        webhook_url(pid, api_trigger_id),
    ):
        assert_error(await api.post(url, json={}), 404, "TRIGGER_NOT_FOUND")


async def test_webhook_rejects_non_object_payload(api: httpx.AsyncClient) -> None:
    trigger_id = new_id("trig")
    pid, _, _ = await publish_with(api, [{"id": trigger_id, "trigger_type": "webhook"}])

    for content in (b"{not json", b"[1, 2]"):
        response = await api.post(
            webhook_url(pid, trigger_id),
            content=content,
            headers={"content-type": "application/json"},
        )
        assert_error(response, 422, "DOMAIN_VALIDATION_ERROR")


async def test_unparseable_or_too_deep_payloads_are_422_not_500(
    api: httpx.AsyncClient,
) -> None:
    """D5: nesting that overflows the JSON parser, bytes that are not UTF-8,
    and nesting deep enough to crash deepcopy (~500 levels) are all client
    errors on every mechanism."""
    webhook_id, api_id = new_id("trig"), new_id("trig")
    pid, version, _ = await publish_with(
        api,
        [
            {"id": webhook_id, "trigger_type": "webhook"},
            {"id": api_id, "trigger_type": "api"},
        ],
    )
    json_headers = {"content-type": "application/json"}
    too_deep = b'{"d": ' + b"[" * 500 + b"]" * 500 + b"}"

    for content in (b"[" * 50_000, b'{"a": "\xff\xfe"}', too_deep):
        response = await api.post(
            webhook_url(pid, webhook_id), content=content, headers=json_headers
        )
        assert_error(response, 422, "DOMAIN_VALIDATION_ERROR")

    for url in (
        invoke_url(pid, api_id),
        f"/api/v1/projects/{pid}/versions/{version}/execute",
    ):
        response = await api.post(url, content=too_deep, headers=json_headers)
        assert response.status_code == 422, response.text
        assert "Traceback" not in response.text


async def test_webhook_payload_limit(
    database: Database, project_service: ProjectService
) -> None:
    async with trigger_client(
        database, project_service, webhook_max_payload_bytes=16
    ) as api:
        trigger_id = new_id("trig")
        pid, _, _ = await publish_with(
            api, [{"id": trigger_id, "trigger_type": "webhook"}]
        )

        response = await api.post(
            webhook_url(pid, trigger_id), json={"too": "big for sixteen bytes"}
        )
        assert_error(response, 413, "PAYLOAD_TOO_LARGE")


async def test_disabled_trigger_is_409(api: httpx.AsyncClient) -> None:
    trigger_id = new_id("trig")
    pid, _, _ = await publish_with(
        api, [{"id": trigger_id, "trigger_type": "webhook", "enabled": False}]
    )

    response = await api.post(webhook_url(pid, trigger_id), json={})
    assert_error(response, 409, "TRIGGER_DISABLED")


async def test_republish_retires_old_trigger(api: httpx.AsyncClient) -> None:
    """Triggers resolve to the latest published version (§125): republishing
    v2 without the trigger retires its v1 endpoint."""
    trigger_id = new_id("trig")
    pid, _, revision = await publish_with(
        api, [{"id": trigger_id, "trigger_type": "webhook"}]
    )
    fired = await api.post(webhook_url(pid, trigger_id), json={})
    assert fired.status_code == 202, fired.text

    await publish_with(api, [], project_id=pid, revision=revision)

    assert_error(
        await api.post(webhook_url(pid, trigger_id), json={}),
        404,
        "TRIGGER_NOT_FOUND",
    )


# -- duplicate and invalid trigger configurations (D3/D6) ----------------------


@pytest.mark.parametrize(
    ("triggers", "code"),
    [
        (
            [
                {"id": "trig_" + "1" * 32, "trigger_type": "webhook"},
                {"id": "trig_" + "1" * 32, "trigger_type": "api"},
            ],
            "duplicate_trigger_id",
        ),
        (
            [
                {
                    "id": "trig_" + "2" * 32,
                    "trigger_type": "cron",
                    "config": {"cron": "0 0 31 2 *"},
                }
            ],
            "invalid_trigger_schedule",
        ),
        (
            [
                {
                    "id": "trig_" + "3" * 32,
                    "trigger_type": "interval",
                    "config": {"interval_seconds": 1e12},
                }
            ],
            "invalid_trigger_schedule",
        ),
    ],
)
async def test_invalid_trigger_configs_block_publish_not_drafts(
    api: httpx.AsyncClient, triggers: list[dict[str, Any]], code: str
) -> None:
    """A draft may hold invalid triggers (it must stay editable); validate
    reports them with their location, and publish refuses them."""
    pid = (await api.post("/api/v1/projects", json={"name": "t"})).json()["id"]
    saved = await api.put(
        f"/api/v1/projects/{pid}",
        json={"expected_revision": 1, "name": "t", "draft": draft_with(triggers)},
    )
    assert saved.status_code == 200, saved.text

    issues = (await api.post(f"/api/v1/projects/{pid}/validate")).json()["issues"]
    assert [(i["code"], i["trigger_id"]) for i in issues] == [
        (code, triggers[-1]["id"])
    ]

    published = await api.post(
        f"/api/v1/projects/{pid}/publish", json={"expected_revision": 2}
    )
    assert_error(published, 422, "PROJECT_VALIDATION_FAILED")
    assert published.json()["details"]["issues"][0]["code"] == code


async def test_same_trigger_id_in_two_projects_is_routed_by_project(
    api: httpx.AsyncClient,
) -> None:
    """D6: trigger ids are unique per project only. A second project that
    reuses an id must never receive the first project's deliveries."""
    shared = new_id("trig")
    first, _, _ = await publish_with(api, [{"id": shared, "trigger_type": "webhook"}])
    second, _, _ = await publish_with(api, [{"id": shared, "trigger_type": "webhook"}])

    for pid in (first, second):
        response = await api.post(webhook_url(pid, shared), json={"to": pid})
        assert response.status_code == 202, response.text
        assert response.json()["project_id"] == pid


async def test_legacy_trigger_configs_stay_loadable_and_isolated(
    api: httpx.AsyncClient, database: Database
) -> None:
    """D4: a trigger stored before the Phase 10 schedule rules (any non-empty
    cron string was accepted) must neither lock its own project nor break
    other projects' webhooks or schedules."""
    healthy_hook, healthy_tick = new_id("trig"), new_id("trig")
    healthy, _, _ = await publish_with(
        api,
        [
            {"id": healthy_hook, "trigger_type": "webhook"},
            {
                "id": healthy_tick,
                "trigger_type": "interval",
                "config": {"interval_seconds": 30},
            },
        ],
    )
    legacy_id = new_id("trig")
    legacy, _, _ = await publish_with(
        api,
        [{"id": legacy_id, "trigger_type": "cron", "config": {"cron": "0 * * * *"}}],
    )
    stale = json.dumps(
        [
            {
                "id": legacy_id,
                "trigger_type": "cron",
                "config": {"cron": "garbage"},
                "enabled": True,
            }
        ]
    )
    async with database.engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE projects SET draft_triggers = CAST(:t AS JSONB) WHERE id = :p"
            ),
            {"t": stale, "p": legacy},
        )
        await conn.execute(
            text(
                "UPDATE project_versions SET triggers = CAST(:t AS JSONB)"
                " WHERE project_id = :p"
            ),
            {"t": stale, "p": legacy},
        )

    # The legacy project stays loadable and tells the user what to fix.
    assert (await api.get(f"/api/v1/projects/{legacy}")).status_code == 200
    assert (await api.get(f"/api/v1/projects/{legacy}/versions/1")).status_code == 200
    issues = (await api.post(f"/api/v1/projects/{legacy}/validate")).json()["issues"]
    assert [(i["code"], i["trigger_id"]) for i in issues] == [
        ("invalid_trigger_schedule", legacy_id)
    ]
    # Other projects are unaffected, and nothing about the legacy config leaks.
    fired = await api.post(webhook_url(healthy, healthy_hook), json={})
    assert fired.status_code == 202, fired.text
    assert "garbage" not in fired.text

    clock = FakeClock(datetime(2026, 1, 5, 12, 0, 0, tzinfo=UTC))
    scheduler = TriggerScheduler(
        TriggerRegistry(lambda: uow_of(database)),
        TriggerService(ExecutionService(lambda: uow_of(database))),
        clock=clock,
    )
    assert await scheduler.tick() == 0  # arms; the legacy cron is parked
    clock.advance(seconds=30)
    assert await scheduler.tick() == 1  # the healthy interval still fires
    rows = await db_executions(database)
    assert {r["metadata"]["trigger_id"] for r in rows} == {
        healthy_hook,
        healthy_tick,
    }


# -- trigger → execution → runtime: the canonical execution really runs ------


class _Source(Node):
    definition = SOURCE

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        return NodeResult({"text": "hello"})


class _Sink(Node):
    definition = SINK

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        return NodeResult({})


async def test_triggered_execution_runs_through_the_same_runtime(
    api: httpx.AsyncClient, database: Database
) -> None:
    trigger_id = new_id("trig")
    pid, _, _ = await publish_with(api, [{"id": trigger_id, "trigger_type": "webhook"}])

    fired = await api.post(webhook_url(pid, trigger_id), json={})

    catalog: NodeCatalog = {"test.source": _Source, "test.sink": _Sink}
    runtime = ExecutionRuntime(lambda: uow_of(database), catalog)
    execution = await asyncio.wait_for(runtime.run(fired.json()["id"]), timeout=30)
    assert execution.status is ExecutionStatus.COMPLETED


# -- scheduler against the real database (fake clock, §62/§63) ---------------


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


async def db_executions(database: Database) -> list[dict[str, Any]]:
    async with database.engine.connect() as conn:
        rows = (
            (await conn.execute(text("SELECT status, metadata, input FROM executions")))
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


async def test_scheduler_fires_interval_trigger_into_db(
    api: httpx.AsyncClient, database: Database
) -> None:
    trigger_id = new_id("trig")
    await publish_with(
        api,
        [
            {
                "id": trigger_id,
                "trigger_type": "interval",
                "config": {"interval_seconds": 30},
            }
        ],
    )
    clock = FakeClock(datetime(2026, 1, 5, 12, 0, 0, tzinfo=UTC))
    scheduler = TriggerScheduler(
        TriggerRegistry(lambda: uow_of(database)),
        TriggerService(ExecutionService(lambda: uow_of(database))),
        clock=clock,
    )

    assert await scheduler.tick() == 0  # arms on first sight
    clock.advance(seconds=29)
    assert await scheduler.tick() == 0
    clock.advance(seconds=1)
    assert await scheduler.tick() == 1

    # The fire converged on ExecutionService: one canonical QUEUED execution,
    # trigger identity in metadata (§73), not created twice.
    rows = await db_executions(database)
    assert len(rows) == 1
    assert rows[0]["status"] == "queued"
    assert rows[0]["metadata"]["trigger_id"] == trigger_id
    assert rows[0]["metadata"]["trigger_type"] == "interval"


async def test_scheduler_cron_fires_at_boundary(
    api: httpx.AsyncClient, database: Database
) -> None:
    trigger = {
        "id": new_id("trig"),
        "trigger_type": "cron",
        "config": {"cron": "* * * * *"},
    }
    await publish_with(api, [trigger])
    clock = FakeClock(datetime(2026, 1, 5, 12, 0, 30, tzinfo=UTC))
    scheduler = TriggerScheduler(
        TriggerRegistry(lambda: uow_of(database)),
        TriggerService(ExecutionService(lambda: uow_of(database))),
        clock=clock,
    )

    assert await scheduler.tick() == 0  # arms; next boundary 12:01:00
    clock.advance(seconds=30)
    assert await scheduler.tick() == 1
    clock.advance(seconds=1)
    assert await scheduler.tick() == 0  # a fired boundary does not refire
    clock.advance(hours=3)  # downtime: missed boundaries are skipped (§99)
    assert await scheduler.tick() == 1

    assert len(await db_executions(database)) == 2
