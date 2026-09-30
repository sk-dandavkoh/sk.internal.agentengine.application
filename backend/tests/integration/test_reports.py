"""Execution reports against real PostgreSQL (roadmap Phase 19).

Executions are driven through the real domain transitions with fixed
timestamps and persisted through the real Unit of Work; every report is then
generated from the database and checked against what was persisted. The
runtime refuses retry policies until Phase 23, so the retry history is
recorded through ``Execution.retry_node`` directly, as the runtime will.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from akasha.api.app import create_app
from akasha.application.executions import ExecutionNotFoundError, save_execution
from akasha.application.reports import ExecutionReport, ReportService, report_json
from akasha.bootstrap import Container
from akasha.config import Settings
from akasha.domain.execution import (
    Execution,
    ExecutionError,
    ExecutionMetadata,
    RetryPolicy,
)
from akasha.domain.graph import Edge, Graph, NodeInstance
from akasha.domain.node import NodeErrorKind
from akasha.domain.project import Project
from akasha.domain.trigger import TriggerType
from akasha.infrastructure.database import Database
from akasha.infrastructure.persistence import SqlAlchemyUnitOfWork
from tests.integration.conftest import AuthKit

UowFactory = Callable[[], AbstractAsyncContextManager[SqlAlchemyUnitOfWork]]

pytestmark = pytest.mark.usefixtures("clean_db")

T0 = datetime(2100, 1, 1, tzinfo=UTC)
UNKNOWN_EXECUTION = "exec_" + "0" * 32


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def llm_outputs(*, cost: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "text": "hello",
        "structured": None,
        "tool_calls": [],
        "metadata": {
            "provider": "fake",
            "model": "fake-1",
            "request_id": "req-1",
            "finish_reason": "stop",
            "usage": {"input_tokens": 12, "output_tokens": 30, "total_tokens": 42},
            "cost": cost,
        },
    }


class Scenario:
    """source -> llm -> sink, published and persisted."""

    def __init__(self, uow: UowFactory) -> None:
        self.uow = uow
        self.source = NodeInstance.create("test.emit", label="Source")
        self.llm = NodeInstance.create("core.llm", label="Model")
        self.sink = NodeInstance.create("test.sink", label="Sink")
        self.graph = Graph()
        for node in (self.source, self.llm, self.sink):
            self.graph.add_node(node)
        self.graph.add_edge(Edge(self.source.id, "out", self.llm.id, "variables"))
        self.graph.add_edge(Edge(self.llm.id, "text", self.sink.id, "text"))
        self.project = Project.create("report demo")
        self.project.draft.graph = self.graph
        self.project.draft.description = "first release"

    async def publish(self) -> None:
        self.version = self.project.publish_draft(published_at=T0)
        async with self.uow() as ctx:
            await ctx.projects.save(self.project)
            await ctx.versions.save(self.project.id, self.version)
            await ctx.commit()

    def execution(self, **kwargs: Any) -> Execution:
        execution = Execution.create(
            self.project.id,
            self.version,
            metadata=ExecutionMetadata(
                trigger_type=TriggerType.API, correlation_id="corr-1"
            ),
            now=at(0),
            **kwargs,
        )
        execution.enqueue()
        execution.start(now=at(1), node_versions={"core.llm": "1.0.0"})
        return execution

    def run_node(
        self, execution: Execution, node_id: str, start: float, outputs: Any
    ) -> None:
        execution.mark_node_ready(node_id)
        execution.start_node(node_id, now=at(start))
        execution.complete_node(node_id, outputs=outputs, now=at(start + 1))

    async def persist(self, execution: Execution) -> None:
        async with self.uow() as ctx:
            await save_execution(ctx, execution)
            await ctx.commit()
        execution.pull_events()

    async def reload(self, execution_id: str) -> Execution:
        async with self.uow() as ctx:
            loaded = await ctx.executions.get(execution_id)
        assert loaded is not None
        return loaded


@pytest.fixture
async def scenario(uow: UowFactory) -> Scenario:
    s = Scenario(uow)
    await s.publish()
    return s


@pytest.fixture
def reports(database: Database) -> ReportService:
    return ReportService(lambda: SqlAlchemyUnitOfWork(database))


def assert_matches_persisted(report: ExecutionReport, persisted: Execution) -> None:
    assert report.execution_id == persisted.id
    assert report.execution.status == persisted.status.value
    assert report.execution.started_at == persisted.started_at
    assert report.execution.finished_at == persisted.finished_at
    assert [n.node_id for n in report.nodes] == sorted(persisted.node_executions)
    for node in report.nodes:
        ne = persisted.node_executions[node.node_id]
        assert node.status == ne.status.value
        assert node.attempts == ne.attempt
        assert len(node.failures) == len(ne.failures)
        assert node.started_at == ne.started_at
        assert node.finished_at == ne.finished_at


async def test_successful_execution_report(
    scenario: Scenario, reports: ReportService
) -> None:
    execution = scenario.execution(input={"topic": "reports"})
    scenario.run_node(execution, scenario.source.id, 2, {"out": "x"})
    scenario.run_node(
        execution,
        scenario.llm.id,
        4,
        llm_outputs(cost={"amount": 0.25, "currency": "USD", "estimated": True}),
    )
    scenario.run_node(execution, scenario.sink.id, 6, {})
    execution.complete(now=at(8))
    await scenario.persist(execution)

    report = await reports.execution_report(execution.id)

    assert_matches_persisted(report, await scenario.reload(execution.id))
    assert report.project.name == "report demo"
    assert report.project.version == 1
    assert report.project.version_description == "first release"
    assert report.execution.status == "completed"
    assert report.execution.trigger_type is TriggerType.API
    assert report.execution.correlation_id == "corr-1"
    assert report.execution.node_versions == {"core.llm": "1.0.0"}
    assert report.execution.duration_ms == 7000
    assert report.execution.input == {"topic": "reports"}
    assert report.execution.error is None
    llm = next(n for n in report.nodes if n.node_id == scenario.llm.id)
    assert (llm.node_type, llm.label, llm.duration_ms) == ("core.llm", "Model", 1000)
    assert llm.outputs is not None and llm.outputs["text"] == "hello"
    assert llm.usage is not None and llm.usage.total_tokens == 42
    assert llm.cost is not None and llm.cost.amount == 0.25
    metrics = report.metrics
    assert metrics.node_status_counts["completed"] == 3
    assert sum(metrics.node_status_counts.values()) == 3
    assert (metrics.attempts, metrics.retries, metrics.failed_attempts) == (3, 0, 0)
    assert metrics.usage.model_dump() == {
        "input_tokens": 12,
        "output_tokens": 30,
        "total_tokens": 42,
    }
    assert [c.model_dump() for c in metrics.costs] == [
        {"amount": 0.25, "currency": "USD"}
    ]
    assert metrics.cost_complete is True
    # Timeline = the persisted event log, in store order.
    types = [e.event_type for e in report.events]
    assert types[0] == "execution.created" and types[-1] == "execution.completed"
    assert types.count("node.completed") == 3
    positions = [e.position for e in report.events]
    assert None not in positions
    assert positions == sorted(p for p in positions if p is not None)
    assert report.events_truncated is False


async def test_failed_execution_report(
    scenario: Scenario, reports: ReportService
) -> None:
    execution = scenario.execution()
    scenario.run_node(execution, scenario.source.id, 2, {"out": "x"})
    execution.mark_node_ready(scenario.llm.id)
    execution.start_node(scenario.llm.id, now=at(4))
    error = ExecutionError(
        code="LLM_PROVIDER_ERROR",
        message="provider rejected the request",
        kind=NodeErrorKind.PERMANENT,
        node_id=scenario.llm.id,
        details={"status": "400"},
    )
    execution.fail_node(scenario.llm.id, error, now=at(5))
    execution.fail(error, now=at(6))
    await scenario.persist(execution)

    report = await reports.execution_report(execution.id)

    assert_matches_persisted(report, await scenario.reload(execution.id))
    assert report.execution.status == "failed"
    assert report.execution.error is not None
    assert report.execution.error.model_dump() == {
        "code": "LLM_PROVIDER_ERROR",
        "message": "provider rejected the request",
        "kind": "permanent",
        "retryable": False,
        "node_id": scenario.llm.id,
        "details": {"status": "400"},
    }
    by_id = {n.node_id: n for n in report.nodes}
    assert by_id[scenario.llm.id].status == "failed"
    assert by_id[scenario.llm.id].error == report.execution.error
    assert by_id[scenario.llm.id].outputs is None
    assert by_id[scenario.sink.id].status == "cancelled"
    counts = report.metrics.node_status_counts
    assert (counts["completed"], counts["failed"], counts["cancelled"]) == (1, 1, 1)
    assert report.metrics.usage.total_tokens == 0
    assert report.metrics.costs == []
    assert report.events[-1].event_type == "execution.failed"
    assert report.events[-1].payload["error_code"] == "LLM_PROVIDER_ERROR"


async def test_partial_execution_report(
    scenario: Scenario, reports: ReportService
) -> None:
    execution = scenario.execution()
    scenario.run_node(execution, scenario.source.id, 2, {"out": "x"})
    execution.mark_node_ready(scenario.llm.id)
    execution.start_node(scenario.llm.id, now=at(4))
    await scenario.persist(execution)

    report = await reports.execution_report(execution.id)

    assert_matches_persisted(report, await scenario.reload(execution.id))
    assert report.execution.status == "running"
    # No clock in reports: an unfinished run has no duration, not a growing one.
    assert report.execution.duration_ms is None
    by_id = {n.node_id: n for n in report.nodes}
    assert by_id[scenario.llm.id].status == "running"
    assert by_id[scenario.llm.id].duration_ms is None
    assert by_id[scenario.sink.id].status == "pending"
    assert by_id[scenario.sink.id].attempts == 0
    counts = report.metrics.node_status_counts
    assert (counts["completed"], counts["running"], counts["pending"]) == (1, 1, 1)


async def test_retry_history_in_report(
    scenario: Scenario, reports: ReportService
) -> None:
    execution = scenario.execution(retry_policy=RetryPolicy(max_attempts=3))
    scenario.run_node(execution, scenario.source.id, 2, {"out": "x"})
    execution.mark_node_ready(scenario.llm.id)
    execution.start_node(scenario.llm.id, now=at(4))
    transient = ExecutionError(
        code="LLM_RATE_LIMITED",
        message="rate limited",
        kind=NodeErrorKind.TRANSIENT,
        node_id=scenario.llm.id,
    )
    backoff = execution.retry_node(scenario.llm.id, transient, now=at(5))
    execution.start_node(scenario.llm.id, now=at(5) + backoff)
    execution.complete_node(scenario.llm.id, outputs=llm_outputs(cost=None), now=at(9))
    scenario.run_node(execution, scenario.sink.id, 10, {})
    execution.complete(now=at(12))
    await scenario.persist(execution)

    report = await reports.execution_report(execution.id)

    assert_matches_persisted(report, await scenario.reload(execution.id))
    llm = next(n for n in report.nodes if n.node_id == scenario.llm.id)
    assert (llm.status, llm.attempts, llm.retries) == ("completed", 2, 1)
    assert llm.error is None  # the retry succeeded
    assert [(f.code, f.retryable) for f in llm.failures] == [("LLM_RATE_LIMITED", True)]
    assert llm.started_at == at(5) + backoff  # the last attempt
    assert (report.metrics.retries, report.metrics.failed_attempts) == (1, 1)
    assert report.metrics.attempts == 4
    # Usage was reported without a price: the cost total is known incomplete.
    assert report.metrics.usage.total_tokens == 42
    assert report.metrics.costs == []
    assert report.metrics.cost_complete is False
    started = [
        e.payload["attempt"]
        for e in report.events
        if e.event_type == "node.started" and e.node_id == scenario.llm.id
    ]
    assert started == [1, 2]


SECRETS = (
    "sk-live-0123456789abcdefghij",
    "hunter2-password",
    "s3cr3t-basic-pass",
    "Zm9vYmFyYmF6cXV4",
    "ghp_" + "a" * 24,
)


async def test_report_redacts_secrets(
    scenario: Scenario, reports: ReportService
) -> None:
    execution = scenario.execution(
        input={"api_key": SECRETS[0], "note": f"use {SECRETS[4]} please"}
    )
    scenario.run_node(
        execution,
        scenario.source.id,
        2,
        {"out": f"password={SECRETS[1]}", "Authorization": f"Bearer {SECRETS[3]}"},
    )
    execution.mark_node_ready(scenario.llm.id)
    execution.start_node(scenario.llm.id, now=at(4))
    error = ExecutionError(
        code="HTTP_ERROR",
        message=f"GET https://admin:{SECRETS[2]}@internal.test failed",
        kind=NodeErrorKind.PERMANENT,
        node_id=scenario.llm.id,
        details={"authorization": f"Bearer {SECRETS[3]}", "hint": SECRETS[0]},
    )
    execution.fail_node(scenario.llm.id, error, now=at(5))
    execution.request_cancellation(reason=f"token={SECRETS[1]}", now=at(6))
    execution.cancel(now=at(6))
    await scenario.persist(execution)

    report = await reports.execution_report(execution.id)
    exported = report_json(report)

    # The report redacts on its own; the persisted row still holds the raw
    # values these (deliberately unmasked) test nodes produced.
    persisted = await scenario.reload(execution.id)
    assert persisted.input["api_key"] == SECRETS[0]
    for secret in SECRETS:
        assert secret not in exported, secret
    assert report.execution.input["api_key"] == "[REDACTED]"
    assert report.execution.cancellation is not None
    assert "[REDACTED]" in report.execution.cancellation.reason
    source = next(n for n in report.nodes if n.node_id == scenario.source.id)
    assert source.outputs is not None
    assert source.outputs["Authorization"] == "[REDACTED]"
    assert source.outputs["out"] == "password=[REDACTED]"
    llm = next(n for n in report.nodes if n.node_id == scenario.llm.id)
    assert llm.error is not None and llm.error.details["authorization"] == "[REDACTED]"
    assert "internal.test" in llm.error.message  # context survives redaction


async def test_export_is_deterministic_and_round_trips(
    scenario: Scenario, reports: ReportService
) -> None:
    execution = scenario.execution(input={"b": 1, "a": [1, 2]})
    scenario.run_node(execution, scenario.source.id, 2, {"out": "ü"})
    execution.cancel(now=at(3))
    await scenario.persist(execution)

    first = report_json(await reports.execution_report(execution.id))
    second = report_json(await reports.execution_report(execution.id))

    assert first == second
    parsed = ExecutionReport.model_validate_json(first)
    assert report_json(parsed) == first
    assert '"ü"' in first  # readable, not \u-escaped


async def test_unknown_execution(reports: ReportService) -> None:
    with pytest.raises(ExecutionNotFoundError):
        await reports.execution_report(UNKNOWN_EXECUTION)


@pytest.fixture
async def api(
    database: Database, reports: ReportService, auth_kit: AuthKit
) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(environment="test", log_level="CRITICAL")
    container = Container(
        settings=settings,
        database=database,
        reports=reports,
        **auth_kit.container_fields(),
    )
    app = create_app(settings=settings, container=container)
    transport = httpx.ASGITransport(app=app)
    headers = await auth_kit.admin_headers()
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=headers
    ) as c:
        yield c


async def test_report_endpoint_serves_the_export(
    scenario: Scenario, reports: ReportService, api: httpx.AsyncClient
) -> None:
    execution = scenario.execution(input={"api_key": SECRETS[0]})
    execution.cancel(now=at(2))
    await scenario.persist(execution)

    response = await api.get(f"/api/v1/reports/executions/{execution.id}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.text == report_json(await reports.execution_report(execution.id))
    assert SECRETS[0] not in response.text


async def test_report_endpoint_unknown_execution(api: httpx.AsyncClient) -> None:
    response = await api.get(f"/api/v1/reports/executions/{UNKNOWN_EXECUTION}")

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "EXECUTION_NOT_FOUND"
    assert set(body) == {"code", "message", "details", "request_id"}
