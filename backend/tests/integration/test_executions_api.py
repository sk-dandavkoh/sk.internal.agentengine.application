"""Execution inspection/control and the node catalog through the real HTTP
stack and PostgreSQL (architecture §22, Phase 27).

App → router → ExecutionService / ExecutionRuntime → Unit of Work →
PostgreSQL. Executions really run through the runtime, so the event history
the API returns is what the runtime persisted.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from akasha.api.app import create_app
from akasha.application.executions import ExecutionService
from akasha.application.projects import ProjectService
from akasha.application.triggers import TriggerRegistry, TriggerService
from akasha.bootstrap import Container
from akasha.config import Settings
from akasha.domain.node import Node, NodeExecutionContext, NodeResult
from akasha.errors import InvalidStateTransitionError
from akasha.infrastructure.database import Database
from akasha.infrastructure.persistence import SqlAlchemyUnitOfWork
from akasha.nodes.builtins import create_builtin_registry
from akasha.runtime.runtime import ExecutionRuntime
from akasha.sdk import NodeError, NodeErrorKind
from tests.integration.conftest import SINK, SOURCE, AuthKit, make_auth_kit
from tests.integration.test_triggers_api import assert_error, publish_with

pytestmark = pytest.mark.usefixtures("clean_db")

UNKNOWN_EXECUTION = "exec_" + "0" * 32
UNKNOWN_PROJECT = "proj_" + "0" * 32
SECRET = "sk-live-0123456789abcdefghijklmnop"


class _Source(Node):
    definition = SOURCE

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        return NodeResult({"text": "hello"})


class _Sink(Node):
    """Fails when the execution input asks for it, with a secret in the
    message: the event history must never return it in plaintext."""

    definition = SINK

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        if context.execution_input.get("fail"):
            raise NodeError(
                f"upstream rejected key {SECRET}", kind=NodeErrorKind.PERMANENT
            )
        return NodeResult({})


@dataclass
class Env:
    client: httpx.AsyncClient
    runtime: ExecutionRuntime
    kit: AuthKit
    admin: dict[str, str]

    async def run(self, project_id: str, version: int, **input: Any) -> str:
        created = await self.client.post(
            f"/api/v1/projects/{project_id}/versions/{version}/execute", json=input
        )
        assert created.status_code == 201, created.text
        execution_id: str = created.json()["id"]
        await self.runtime.run(execution_id)
        return execution_id


@pytest.fixture
async def env(
    database: Database, project_service: ProjectService
) -> AsyncIterator[Env]:
    def uow() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(database)

    settings = Settings(environment="test", log_level="CRITICAL")
    kit = make_auth_kit(database)
    executions = ExecutionService(uow)
    runtime = ExecutionRuntime(uow, {"test.source": _Source, "test.sink": _Sink})
    container = Container(
        settings=settings,
        nodes=create_builtin_registry(),
        database=database,
        projects=project_service,
        executions=executions,
        runtime=runtime,
        triggers=TriggerService(executions),
        trigger_registry=TriggerRegistry(uow),
        **kit.container_fields(),
    )
    app = create_app(settings=settings, container=container)
    admin = await kit.admin_headers()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=admin,
    ) as client:
        yield Env(client, runtime, kit, admin)


# -- list -----------------------------------------------------------------------


async def test_list_executions_newest_first_and_paginated(env: Env) -> None:
    pid, version, _ = await publish_with(env.client, [])
    assert (await env.client.get(f"/api/v1/projects/{pid}/executions")).json() == {
        "items": [],
        "page": {"offset": 0, "limit": 50, "has_more": False},
    }
    ids = [await env.run(pid, version) for _ in range(3)]

    first = (
        await env.client.get(f"/api/v1/projects/{pid}/executions", params={"limit": 2})
    ).json()
    second = (
        await env.client.get(
            f"/api/v1/projects/{pid}/executions", params={"offset": 2, "limit": 2}
        )
    ).json()

    listed = [e["id"] for e in first["items"] + second["items"]]
    assert listed == ids[::-1]
    assert (first["page"]["has_more"], second["page"]["has_more"]) == (True, False)
    item = first["items"][0]
    assert (item["status"], item["version"], item["trigger_type"]) == (
        "completed",
        version,
        "manual",
    )
    assert item["started_at"] is not None and item["finished_at"] is not None


async def test_list_executions_rejects_bad_pages_and_unknown_projects(
    env: Env,
) -> None:
    pid, _, _ = await publish_with(env.client, [])
    for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
        response = await env.client.get(
            f"/api/v1/projects/{pid}/executions", params=params
        )
        assert response.status_code == 422, params
    assert_error(
        await env.client.get(f"/api/v1/projects/{UNKNOWN_PROJECT}/executions"),
        404,
        "PROJECT_NOT_FOUND",
    )


# -- events ---------------------------------------------------------------------


async def test_event_history_is_ordered_and_resumable(env: Env) -> None:
    pid, version, _ = await publish_with(env.client, [])
    execution_id = await env.run(pid, version)
    url = f"/api/v1/executions/{execution_id}/events"

    everything = (await env.client.get(url)).json()
    types = [e["event_type"] for e in everything["items"]]
    assert types[0] == "execution.created"
    assert types[-1] == "execution.completed"
    assert types.count("node.completed") == 2
    positions = [e["position"] for e in everything["items"]]
    assert positions == sorted(positions)
    assert everything["has_more"] is False
    assert everything["next_after"] == positions[-1]
    assert {e["execution_id"] for e in everything["items"]} == {execution_id}

    # Page through with the cursor: the same history, nothing twice.
    seen: list[int] = []
    after = 0
    while True:
        page = (await env.client.get(url, params={"after": after, "limit": 2})).json()
        seen += [e["position"] for e in page["items"]]
        after = page["next_after"]
        if not page["has_more"]:
            break
    assert seen == positions
    caught_up = (await env.client.get(url, params={"after": after})).json()
    assert (caught_up["items"], caught_up["next_after"]) == ([], after)


async def test_event_history_never_returns_secrets(env: Env) -> None:
    pid, version, _ = await publish_with(env.client, [])
    execution_id = await env.run(pid, version, fail=True)

    response = await env.client.get(f"/api/v1/executions/{execution_id}/events")

    assert response.status_code == 200
    assert "node.failed" in {e["event_type"] for e in response.json()["items"]}
    assert SECRET not in response.text


async def test_events_of_unknown_execution_is_404(env: Env) -> None:
    assert_error(
        await env.client.get(f"/api/v1/executions/{UNKNOWN_EXECUTION}/events"),
        404,
        "EXECUTION_NOT_FOUND",
    )


# -- cancel ---------------------------------------------------------------------


async def test_cancel_queued_execution_then_conflict(env: Env) -> None:
    pid, version, _ = await publish_with(env.client, [])
    created = await env.client.post(
        f"/api/v1/projects/{pid}/versions/{version}/execute", json={}
    )
    execution_id = created.json()["id"]
    url = f"/api/v1/executions/{execution_id}/cancel"

    cancelled = await env.client.post(url, json={"reason": "not needed"})

    assert cancelled.status_code == 202, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    events = (await env.client.get(f"/api/v1/executions/{execution_id}/events")).json()
    assert events["items"][-1]["event_type"] == "execution.cancelled"
    # A finished execution cannot be cancelled again; the runtime never
    # revives it either.
    assert_error(await env.client.post(url), 409, "INVALID_STATE_TRANSITION")
    with pytest.raises(InvalidStateTransitionError):
        await env.runtime.run(execution_id)


async def test_cancel_completed_or_unknown_execution(env: Env) -> None:
    pid, version, _ = await publish_with(env.client, [])
    execution_id = await env.run(pid, version)
    assert_error(
        await env.client.post(f"/api/v1/executions/{execution_id}/cancel"),
        409,
        "INVALID_STATE_TRANSITION",
    )
    assert_error(
        await env.client.post(f"/api/v1/executions/{UNKNOWN_EXECUTION}/cancel"),
        404,
        "EXECUTION_NOT_FOUND",
    )
    response = await env.client.post(
        f"/api/v1/executions/{execution_id}/cancel", json={"reason": "x" * 501}
    )
    assert response.status_code == 422


# -- node catalog -----------------------------------------------------------------


async def test_node_catalog_lists_every_registered_type(env: Env) -> None:
    member = await env.kit.member_headers(env.admin, "member")

    response = await env.client.get("/api/v1/nodes", headers=member)

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    node_types = [n["node_type"] for n in items]
    assert node_types == sorted(create_builtin_registry().definitions)
    llm = next(n for n in items if n["node_type"] == "core.llm")
    assert llm["category"] == "ai"
    assert {"provider", "model", "user_prompt"} <= set(llm["config_schema"]["required"])
    variables = next(p for p in llm["inputs"] if p["name"] == "variables")
    assert (variables["type"], variables["required"]) == ("object", False)
    search = next(n for n in items if n["node_type"] == "core.web_search")
    assert search["outputs"][0]["schema"]["type"] == "array"
    flags = next(n for n in items if n["node_type"] == "core.if")["capabilities"]
    assert flags["is_control_flow"] is True
