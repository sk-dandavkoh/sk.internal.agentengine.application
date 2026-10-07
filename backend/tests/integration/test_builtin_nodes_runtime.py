"""Built-in nodes through the real stack (roadmap Phase 09 acceptance):
NodeRegistry → WorkflowService (publish-time validation) → ExecutionService
→ ExecutionRuntime → PostgreSQL, with local HTTP/search/SMTP services.

Everything is asserted on the *persisted* execution. The runtime never
learns node types: branching and skipping below come from the generic
control-flow rule (``ExecutionPlan.readiness``).
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import pytest

from akasha.application.executions import ExecutionService
from akasha.application.workflows import WorkflowService, WorkflowValidationFailedError
from akasha.domain.execution import (
    Execution,
    ExecutionStatus,
    NodeExecutionStatus,
    TimeoutPolicy,
)
from akasha.domain.graph import Edge, Graph, NodeInstance
from akasha.domain.node import NodeErrorKind, NodeServices
from akasha.domain.workflow import Dependency, WorkflowDraft
from akasha.infrastructure.database import Database
from akasha.infrastructure.persistence import SqlAlchemyUnitOfWork
from akasha.nodes.builtins import create_builtin_registry
from akasha.nodes.registry import NodeRegistry
from akasha.runtime.runtime import ExecutionRuntime
from akasha.security.network import NetworkPolicy
from tests.support import LocalHttpServer, Mailpit, docker_available, run_mailpit

pytestmark = pytest.mark.usefixtures("clean_db")

_E, _N = ExecutionStatus, NodeExecutionStatus
LOCAL = NetworkPolicy(allow_private_networks=True)


@pytest.fixture(scope="module")
def open_mailpit() -> Iterator[Mailpit]:
    """Anonymous SMTP: the runtime has no credential resolver before
    Phase 13, so authenticated delivery is covered in test_smtp_node."""
    if not docker_available():
        pytest.skip("Docker daemon not available for Testcontainers")
    with run_mailpit(auth=False) as server:
        yield server


@dataclass
class Engine:
    database: Database
    registry: NodeRegistry
    services: NodeServices = field(default_factory=NodeServices)

    def uow(self) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self.database)

    @property
    def workflows(self) -> WorkflowService:
        return WorkflowService(self.uow, self.registry.definitions)

    async def publish(self, draft: WorkflowDraft) -> tuple[str, int]:
        workflow = await self.workflows.create_workflow("builtins")
        await self.workflows.update_draft(
            workflow.id,
            expected_revision=workflow.revision,
            name="builtins",
            draft=draft,
        )
        version = await self.workflows.publish_workflow(workflow.id)
        return workflow.id, version.version

    async def run(
        self,
        graph: Graph,
        *,
        input: dict[str, Any] | None = None,
        timeout_policy: TimeoutPolicy | None = None,
    ) -> Execution:
        workflow_id, version = await self.publish(WorkflowDraft(graph=graph))
        executions = ExecutionService(self.uow)
        queued = await executions.create_execution(
            workflow_id, version, input=input, timeout_policy=timeout_policy
        )
        await ExecutionRuntime(
            self.uow, self.registry.catalog, services=self.services
        ).run(queued.id)
        return await executions.get_execution(queued.id)  # persisted state


def build(
    nodes: dict[str, tuple[str, dict[str, Any]]], edges: list[str]
) -> tuple[Graph, dict[str, str]]:
    """``nodes``: label -> (type, config); ``edges``: "a.out>b.in"."""
    graph, ids = Graph(), {}
    for label, (node_type, config) in nodes.items():
        instance = NodeInstance.create(node_type, config=config, label=label)
        graph.add_node(instance)
        ids[label] = instance.id
    for spec in edges:
        (src, sport), (dst, dport) = (side.split(".") for side in spec.split(">"))
        graph.add_edge(Edge(ids[src], sport, ids[dst], dport))
    return graph, ids


def status(execution: Execution, ids: dict[str, str]) -> dict[str, NodeExecutionStatus]:
    return {label: execution.node(node_id).status for label, node_id in ids.items()}


def value(execution: Execution, node_id: str) -> Any:
    outputs = execution.node(node_id).outputs
    assert outputs is not None
    return outputs["value"]


@pytest.fixture
def engine(database: Database) -> Engine:
    return Engine(database, create_builtin_registry(), NodeServices(network=LOCAL))


def lead_router() -> tuple[Graph, dict[str, str]]:
    """input(score) → if(>0.5) → true: log → hot / false: note → cold."""
    return build(
        {
            "input": ("core.input", {"field": "score"}),
            "if": ("core.if", {"operator": "greater_than", "operand": 0.5}),
            "log": ("core.log", {"message": "hot lead"}),
            "hot": ("core.output", {}),
            "note": ("core.log", {"message": "cold lead"}),
            "cold": ("core.output", {}),
        },
        [
            "input.value>if.value",
            "if.true>log.value",
            "log.value>hot.value",
            "if.false>note.value",
            "note.value>cold.value",
        ],
    )


# -- control flow ----------------------------------------------------------------


async def test_if_runs_the_taken_branch_and_skips_the_other_transitively(
    engine: Engine,
) -> None:
    graph, ids = lead_router()
    execution = await engine.run(graph, input={"score": 0.9})

    assert execution.status is _E.COMPLETED
    assert status(execution, ids) == {
        "input": _N.COMPLETED,
        "if": _N.COMPLETED,
        "log": _N.COMPLETED,
        "hot": _N.COMPLETED,
        "note": _N.SKIPPED,
        "cold": _N.SKIPPED,  # skipped because its only upstream was skipped
    }
    assert value(execution, ids["hot"]) == 0.9
    assert execution.node(ids["if"]).outputs == {"true": 0.9}  # no false port
    assert execution.input == {"score": 0.9}
    assert execution.metadata.node_versions == {
        "core.if": "1.1.0",
        "core.input": "1.0.0",
        "core.log": "1.0.0",
        "core.output": "1.0.0",
    }


async def test_if_false_branch(engine: Engine) -> None:
    graph, ids = lead_router()
    execution = await engine.run(graph, input={"score": 0.1})
    assert execution.status is _E.COMPLETED
    assert (status(execution, ids)["hot"], status(execution, ids)["cold"]) == (
        _N.SKIPPED,
        _N.COMPLETED,
    )
    assert value(execution, ids["cold"]) == 0.1


async def test_switch_and_merge_join_only_the_live_branch(engine: Engine) -> None:
    graph, ids = build(
        {
            "input": ("core.input", {"field": "tier"}),
            "switch": ("core.switch", {"cases": ["gold", "silver"]}),
            "wait": ("core.delay", {"seconds": 0}),
            "merge": ("core.merge", {}),
            "out": ("core.output", {}),
            "other": ("core.output", {}),
        },
        [
            "input.value>switch.value",
            "switch.case_0>wait.value",
            "wait.value>merge.items",
            "switch.case_1>merge.items",
            "merge.items>out.value",
            "switch.default>other.value",
        ],
    )
    execution = await engine.run(graph, input={"tier": "silver"})
    assert execution.status is _E.COMPLETED
    assert status(execution, ids) == {
        "input": _N.COMPLETED,
        "switch": _N.COMPLETED,
        "wait": _N.SKIPPED,
        "merge": _N.COMPLETED,  # one live branch suffices
        "out": _N.COMPLETED,
        "other": _N.SKIPPED,
    }
    assert value(execution, ids["out"]) == ["silver"]

    gold = await engine.run(graph, input={"tier": "gold"})
    assert value(gold, ids["out"]) == ["gold"]  # via the delay branch
    assert status(gold, ids)["wait"] is _N.COMPLETED


async def test_node_failure_fails_the_execution_with_its_code(engine: Engine) -> None:
    graph, ids = lead_router()
    execution = await engine.run(graph, input={"score": "high"})  # not a number
    assert execution.status is _E.FAILED
    assert execution.error is not None
    assert (execution.error.code, execution.error.node_id) == (
        "CONDITION_TYPE_MISMATCH",
        ids["if"],
    )
    assert status(execution, ids)["hot"] is _N.CANCELLED


async def test_missing_input_field(engine: Engine) -> None:
    graph, _ = lead_router()
    execution = await engine.run(graph, input={})
    assert execution.error is not None
    assert execution.error.code == "INPUT_FIELD_MISSING"


async def test_delay_is_bounded_by_the_node_timeout(engine: Engine) -> None:
    graph, ids = build(
        {"wait": ("core.delay", {"seconds": 3600}), "out": ("core.output", {})},
        ["wait.value>out.value"],
    )
    execution = await engine.run(
        graph, timeout_policy=TimeoutPolicy(node_timeout=timedelta(seconds=0.2))
    )
    assert execution.status is _E.FAILED
    assert execution.error is not None
    assert (execution.error.code, execution.error.kind) == (
        "NODE_TIMEOUT",
        NodeErrorKind.TIMEOUT,
    )
    assert status(execution, ids) == {"wait": _N.FAILED, "out": _N.CANCELLED}


async def test_log_lines_carry_the_execution_id(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="akasha.nodes")
    graph, _ = lead_router()
    execution = await engine.run(graph, input={"score": 0.9})
    lines = [r for r in caplog.records if r.__dict__.get("event") == "node_log"]
    assert [r.getMessage() for r in lines] == ["hot lead"]
    assert lines[0].__dict__["execution_id"] == execution.id


# -- network nodes ---------------------------------------------------------------


async def test_http_post_and_output(
    engine: Engine, http_server: LocalHttpServer
) -> None:
    graph, ids = build(
        {
            "input": ("core.input", {}),
            "http": (
                "core.http",
                {"method": "POST", "url": f"{http_server.base_url}/echo"},
            ),
            "out": ("core.output", {}),
        },
        ["input.value>http.body", "http.body>out.value"],
    )
    execution = await engine.run(graph, input={"lead": "Ada"})
    assert execution.status is _E.COMPLETED
    assert value(execution, ids["out"])["body"] == '{"lead": "Ada"}'
    http_outputs = execution.node(ids["http"]).outputs
    assert http_outputs is not None and http_outputs["status"] == 200
    assert http_server.state.requests[0].method == "POST"


async def test_http_error_status_fails_the_execution_as_transient(
    engine: Engine, http_server: LocalHttpServer
) -> None:
    graph, _ = build(
        {
            "http": ("core.http", {"url": f"{http_server.base_url}/status/503"}),
            "out": ("core.output", {}),
        },
        ["http.status>out.value"],
    )
    execution = await engine.run(graph)
    assert execution.error is not None
    assert (execution.error.code, execution.error.kind) == (
        "HTTP_STATUS_ERROR",
        NodeErrorKind.TRANSIENT,
    )
    assert execution.error.details == {"status": "503"}


async def test_ssrf_guard_applies_inside_the_runtime(
    database: Database, http_server: LocalHttpServer
) -> None:
    strict = Engine(database, create_builtin_registry())  # default policy
    graph, _ = build(
        {
            "http": ("core.http", {"url": f"{http_server.base_url}/echo"}),
            "out": ("core.output", {}),
        },
        ["http.status>out.value"],
    )
    execution = await strict.run(graph)
    assert execution.error is not None
    assert execution.error.code == "DESTINATION_BLOCKED"
    assert http_server.state.requests == []


async def test_web_search_results_propagate(
    database: Database, http_server: LocalHttpServer
) -> None:
    http_server.state.search_body = (
        b'{"results": [{"title": "T", "url": "https://example.test/", "content": "S"}]}'
    )
    engine = Engine(
        database,
        create_builtin_registry(),
        NodeServices(settings={"core": {"web_search_url": http_server.base_url}}),
    )
    graph, ids = build(
        {
            "input": ("core.input", {"field": "q"}),
            "search": ("core.web_search", {"max_results": 3}),
            "out": ("core.output", {}),
        },
        ["input.value>search.query", "search.results>out.value"],
    )
    execution = await engine.run(graph, input={"q": "akasha"})
    assert execution.status is _E.COMPLETED
    assert value(execution, ids["out"]) == [
        {"title": "T", "url": "https://example.test/", "snippet": "S"}
    ]


async def test_smtp_sends_through_the_runtime(
    engine: Engine, open_mailpit: Mailpit
) -> None:
    open_mailpit.clear()
    graph, ids = build(
        {
            "subject": ("core.input", {"field": "subject"}),
            "body": ("core.input", {"field": "body"}),
            "mail": (
                "core.smtp",
                {
                    "host": open_mailpit.smtp_host,
                    "port": open_mailpit.smtp_port,
                    "security": "none",
                    "sender": "engine@akasha.test",
                    "recipients": ["ada@example.test"],
                },
            ),
            "out": ("core.output", {}),
        },
        [
            "subject.value>mail.subject",
            "body.value>mail.body",
            "mail.message_id>out.value",
        ],
    )
    execution = await engine.run(graph, input={"subject": "Hi", "body": "Hello Ada"})
    assert execution.status is _E.COMPLETED
    (summary,) = open_mailpit.messages()
    assert summary["Subject"] == "Hi"
    node_execution_id = execution.node(ids["mail"]).id
    assert value(execution, ids["out"]) == f"<{node_execution_id}@akasha>"


async def test_smtp_credential_reference_without_resolver_fails_cleanly(
    engine: Engine,
) -> None:
    graph, _ = build(
        {
            "subject": ("core.input", {"field": "s"}),
            "mail": (
                "core.smtp",
                {
                    "host": "127.0.0.1",
                    "username": "u",
                    "password_credential": "credential:smtp",
                    "sender": "a@akasha.test",
                    "recipients": ["b@akasha.test"],
                },
            ),
        },
        ["subject.value>mail.subject", "subject.value>mail.body"],
    )
    execution = await engine.run(graph, input={"s": "x"})
    assert execution.error is not None
    assert (execution.error.code, execution.error.kind) == (
        "SMTP_CREDENTIALS_UNAVAILABLE",
        NodeErrorKind.CONFIGURATION,
    )


# -- registry lifecycle, validation and versions ---------------------------------


async def test_invalid_builtin_config_is_rejected_at_publish(engine: Engine) -> None:
    graph, _ = build(
        {
            "http": ("core.http", {"url": "file:///etc/passwd"}),
            "out": ("core.output", {}),
        },
        ["http.status>out.value"],
    )
    with pytest.raises(WorkflowValidationFailedError) as exc:
        await engine.publish(WorkflowDraft(graph=graph))
    assert [i.code.value for i in exc.value.result.issues] == ["invalid_config"]


async def test_incompatible_node_version_blocks_publish(engine: Engine) -> None:
    graph, _ = lead_router()
    draft = WorkflowDraft(
        graph=graph, dependencies=[Dependency("node", "core.if", ">=2.0.0")]
    )
    with pytest.raises(WorkflowValidationFailedError) as exc:
        await engine.publish(draft)
    assert [i.code.value for i in exc.value.result.issues] == [
        "incompatible_dependency"
    ]
    compatible = WorkflowDraft(
        graph=graph, dependencies=[Dependency("node", "core.if", ">=1.0.0,<2.0.0")]
    )
    await engine.publish(compatible)


async def test_node_unregistered_after_publish_fails_before_any_node_runs(
    engine: Engine,
) -> None:
    graph, ids = lead_router()
    workflow_id, version = await engine.publish(WorkflowDraft(graph=graph))
    executions = ExecutionService(engine.uow)
    queued = await executions.create_execution(workflow_id, version, input={"score": 1})
    engine.registry.unregister("core.log")

    await ExecutionRuntime(engine.uow, engine.registry.catalog).run(queued.id)

    execution = await executions.get_execution(queued.id)
    assert execution.status is _E.FAILED
    assert execution.error is not None
    assert execution.error.code == "EXECUTION_GRAPH_INVALID"
    assert "unknown_node_type" in execution.error.details["issue_codes"]
    assert set(status(execution, ids).values()) == {_N.CANCELLED}
