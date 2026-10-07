"""Node test runs through the real HTTP stack and PostgreSQL (ADR 0010).

App → router → NodeTester → WorkflowService → PostgreSQL for the draft;
the node runs through the real executor. Nothing about the test is
persisted: no execution appears.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from akasha.api.app import create_app
from akasha.application.executions import ExecutionService
from akasha.application.workflows import WorkflowService
from akasha.bootstrap import Container
from akasha.config import Settings
from akasha.domain.limits import ResourceLimits
from akasha.domain.node import (
    Node,
    NodeCapabilities,
    NodeConfigSchema,
    NodeDefinition,
    NodeError,
    NodeErrorKind,
    NodeExecutionContext,
    NodeInput,
    NodeMetadata,
    NodeOutput,
    NodeResult,
    PortType,
)
from akasha.infrastructure.database import Database
from akasha.infrastructure.persistence import SqlAlchemyUnitOfWork
from akasha.nodes.builtins import create_builtin_registry
from akasha.runtime.node_test import NodeTester
from tests.integration.fixtures import AuthKit, make_auth_kit
from tests.integration.test_triggers_api import assert_error

pytestmark = pytest.mark.usefixtures("clean_db")

SECRET = "sk-live-0123456789abcdefghijklmnop"
ECHO_ID = "node_" + "1" * 32
ASK_ID = "node_" + "2" * 32
GONE_ID = "node_" + "3" * 32
MISSING_ID = "node_" + "9" * 32


class _Echo(Node):
    """Prefixes its text; fails on request, leaking a secret it must not."""

    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="test.echo", name="Echo", description="Echoes.", version="1.0.0"
        ),
        inputs=(NodeInput("text", PortType.STRING, description="Text."),),
        outputs=(
            NodeOutput("text", PortType.STRING, description="Text."),
            NodeOutput("api_key", PortType.STRING, description="A secret."),
        ),
        config_schema=NodeConfigSchema(
            {
                "type": "object",
                "properties": {"prefix": {"type": "string"}},
                "additionalProperties": False,
            }
        ),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        context.logger.warning("echoing with key %s", SECRET)
        text = context.inputs["text"]
        if text == "fail":
            raise NodeError(f"refused key {SECRET}", kind=NodeErrorKind.PERMANENT)
        prefix = context.config.get("prefix", "")
        run = context.execution_input.get("run", "")
        return NodeResult({"text": f"{prefix}{text}{run}", "api_key": SECRET})


class _Ask(Node):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="test.ask", name="Ask", description="Asks.", version="1.0.0"
        ),
        outputs=(NodeOutput("answer", PortType.JSON, description="Answer."),),
        capabilities=NodeCapabilities(is_control_flow=True, waits_for_input=True),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        raise AssertionError("never runs alone")


@dataclass
class Env:
    client: httpx.AsyncClient
    app: Any
    kit: AuthKit
    admin: dict[str, str]
    workflow_id: str
    revision: int

    async def test(self, node_id: str = ECHO_ID, **body: Any) -> httpx.Response:
        return await self.client.post(
            f"/api/v1/workflows/{self.workflow_id}/nodes/{node_id}/test",
            json={"expected_revision": self.revision, **body},
        )


def _node(node_id: str, node_type: str, config: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": node_id,
        "node_type": node_type,
        "config": config,
        "label": "",
        "position": None,
    }


@pytest.fixture
async def env(database: Database) -> AsyncIterator[Env]:
    def uow() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(database)

    settings = Settings(environment="test", log_level="CRITICAL")
    catalog = {"test.echo": _Echo, "test.ask": _Ask}
    workflows = WorkflowService(uow, {k: v.definition for k, v in catalog.items()})
    kit = make_auth_kit(database)
    container = Container(
        settings=settings,
        nodes=create_builtin_registry(),
        database=database,
        workflows=workflows,
        executions=ExecutionService(uow),
        node_tester=NodeTester(
            workflows, catalog, credentials=None, limits=ResourceLimits()
        ),
        **kit.container_fields(),
    )
    app = create_app(settings=settings, container=container)
    admin = await kit.admin_headers()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers=admin
    ) as client:
        workflow_id = (
            await client.post("/api/v1/workflows", json={"name": "probe"})
        ).json()["id"]
        updated = await client.put(
            f"/api/v1/workflows/{workflow_id}",
            json={
                "expected_revision": 1,
                "name": "probe",
                "draft": {
                    "graph": {
                        "nodes": [
                            _node(ECHO_ID, "test.echo", {"prefix": "> "}),
                            _node(ASK_ID, "test.ask", {}),
                            _node(GONE_ID, "test.removed", {}),
                        ],
                        "edges": [],
                    },
                    "triggers": [],
                    "dependencies": [],
                },
            },
        )
        assert updated.status_code == 200, updated.text
        yield Env(client, app, kit, admin, workflow_id, 2)


async def test_runs_the_draft_node_once_and_persists_nothing(env: Env) -> None:
    response = await env.test(inputs={"text": "hi"}, execution_input={"run": "!"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    # The saved draft config (prefix), the given inputs and run input.
    assert body["outputs"]["text"] == "> hi!"
    assert body["error"] is None
    assert body["duration_ms"] >= 0
    # Secrets never come back: not in outputs, not in log lines.
    assert SECRET not in response.text
    assert body["outputs"]["api_key"] == "[REDACTED]"
    assert [line["level"] for line in body["logs"]] == ["WARNING"]
    executions = await env.client.get(f"/api/v1/workflows/{env.workflow_id}/executions")
    assert executions.json()["items"] == []


async def test_a_node_failure_is_a_result_with_a_redacted_error(env: Env) -> None:
    response = await env.test(inputs={"text": "fail"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["outputs"]) == ("failed", None)
    assert body["error"]["kind"] == "permanent"
    assert "refused key" in body["error"]["message"]
    assert SECRET not in response.text


async def test_inputs_and_config_are_checked_like_in_a_run(env: Env) -> None:
    missing = (await env.test(inputs={})).json()
    assert missing["status"] == "failed"
    assert missing["error"]["code"] == "NODE_INPUT_INVALID"

    wrong_type = (await env.test(inputs={"text": 5})).json()
    assert wrong_type["error"]["code"] == "NODE_INPUT_INVALID"


async def test_refusals(env: Env) -> None:
    assert_error(
        await env.test(inputs={"text": "x"}, expected_revision=1),
        409,
        "CONCURRENT_MODIFICATION",
    )
    assert_error(await env.test(MISSING_ID), 404, "NODE_NOT_FOUND")
    # Nothing could answer a waiting node in a test.
    assert_error(await env.test(ASK_ID), 422, "NODE_NOT_TESTABLE")
    assert_error(await env.test(GONE_ID), 422, "NODE_NOT_TESTABLE")


async def test_only_editors_may_test(env: Env) -> None:
    viewer = await env.kit.member_headers(env.admin, "vera")
    stranger = await env.kit.member_headers(env.admin, "sam")
    shared = await env.client.put(
        f"/api/v1/workflows/{env.workflow_id}/members/vera", json={"role": "viewer"}
    )
    assert shared.status_code in (200, 201, 204), shared.text
    body = {"expected_revision": env.revision, "inputs": {"text": "x"}}
    url = f"/api/v1/workflows/{env.workflow_id}/nodes/{ECHO_ID}/test"
    for headers, expected in ((viewer, 403), (stranger, 404)):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=env.app),
            base_url="http://test",
            headers=headers,
        ) as other:
            assert (await other.post(url, json=body)).status_code == expected
