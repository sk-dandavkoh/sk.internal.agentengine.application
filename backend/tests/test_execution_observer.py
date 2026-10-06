"""Execution observer: committed lifecycle events -> log lines + metrics.

Drives the real ``Execution`` aggregate with fixed timestamps, so every
duration is exact.
"""

from datetime import UTC, datetime, timedelta

from akasha.domain.execution import Execution, ExecutionError, ExecutionMetadata
from akasha.domain.graph import Edge, Graph, NodeInstance
from akasha.domain.node import NodeErrorKind
from akasha.domain.primitives import new_id
from akasha.domain.trigger import TriggerType
from akasha.domain.workflow import WorkflowDraft
from akasha.observability.execution import observe_execution
from akasha.observability.metrics import EngineMetrics
from tests.support import capture_json_logs

T0 = datetime(2100, 1, 1, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def two_nodes() -> tuple[Execution, str, str]:
    graph = Graph()
    a = NodeInstance.create("test.source", label="A")
    b = NodeInstance.create("test.sink", label="B")
    graph.add_node(a)
    graph.add_node(b)
    graph.add_edge(Edge(a.id, "out", b.id, "in"))
    execution = Execution.create(
        new_id("wf"),
        WorkflowDraft(graph=graph).publish(version=2),
        metadata=ExecutionMetadata(
            trigger_type=TriggerType.WEBHOOK, correlation_id="corr-9"
        ),
        now=T0,
    )
    execution.enqueue()
    return execution, a.id, b.id


def sample(m: EngineMetrics, name: str, **labels: str) -> float | None:
    return m.registry.get_sample_value(name, labels)


def test_successful_run_logs_every_transition_with_ids_and_timings() -> None:
    metrics = EngineMetrics()
    execution, a, b = two_nodes()
    with capture_json_logs() as lines:
        observe_execution(execution, execution.pull_events(), metrics)  # created
        execution.start(now=at(2))
        execution.mark_node_ready(a)
        execution.start_node(a, now=at(3))
        execution.complete_node(a, outputs={"out": "sensitive-output"}, now=at(3.25))
        execution.mark_node_ready(b)
        execution.start_node(b, now=at(4))
        execution.complete_node(b, outputs={}, now=at(5))
        execution.complete(now=at(6))
        observe_execution(execution, execution.pull_events(), metrics)

    ours = [ln for ln in lines if ln["logger"] == "akasha.execution"]
    assert [ln["event"] for ln in ours] == [
        "execution.created",
        "execution.started",
        "node.started",
        "node.completed",
        "node.started",
        "node.completed",
        "execution.completed",
    ]
    for line in ours:
        assert line["execution_id"] == execution.id
        assert line["workflow_id"] == execution.workflow_id
        assert line["workflow_version"] == 2
        assert line["correlation_id"] == "corr-9"
        assert line["event_id"].startswith("evt_")
    node_lines = [ln for ln in ours if ln["event"].startswith("node.")]
    for line in node_lines:
        ne = execution.node(line["node_id"])
        assert line["node_execution_id"] == ne.id
    assert [ln["node_type"] for ln in node_lines] == ["test.source"] * 2 + [
        "test.sink"
    ] * 2
    assert ours[0]["trigger_type"] == "webhook"
    assert ours[1]["queue_wait_ms"] == 2000
    assert ours[3]["duration_ms"] == 250
    assert ours[5]["duration_ms"] == 1000
    assert ours[6]["duration_ms"] == 4000
    assert "sensitive-output" not in str(lines)

    assert (
        sample(metrics, "akasha_executions_created_total", trigger_type="webhook") == 1
    )
    assert sample(metrics, "akasha_executions_started_total") == 1
    assert sample(metrics, "akasha_executions_finished_total", status="completed") == 1
    assert (
        sample(metrics, "akasha_execution_duration_seconds_sum", status="completed")
        == 4.0
    )
    assert sample(metrics, "akasha_execution_queue_wait_seconds_sum") == 2.0
    assert (
        sample(
            metrics,
            "akasha_node_executions_finished_total",
            node_type="test.source",
            status="completed",
        )
        == 1
    )
    assert (
        sample(
            metrics,
            "akasha_node_execution_duration_seconds_sum",
            node_type="test.source",
            status="completed",
        )
        == 0.25
    )


def test_failure_is_diagnosable_from_the_log_alone() -> None:
    metrics = EngineMetrics()
    execution, a, b = two_nodes()
    execution.pull_events()
    error = ExecutionError(
        code="HTTP_STATUS_ERROR",
        message="upstream answered 503",
        kind=NodeErrorKind.TRANSIENT,
        node_id=a,
    )
    with capture_json_logs() as lines:
        execution.start(now=at(1))
        execution.mark_node_ready(a)
        execution.start_node(a, now=at(1))
        execution.fail_node(a, error, now=at(3))
        execution.fail(error, now=at(3))
        observe_execution(execution, execution.pull_events(), metrics)

    by_event = {ln["event"]: ln for ln in lines}
    node_failed = by_event["node.failed"]
    assert node_failed["level"] == "WARNING"
    assert node_failed["node_id"] == a
    assert node_failed["error_code"] == "HTTP_STATUS_ERROR"
    assert node_failed["error_kind"] == "transient"
    assert node_failed["error_message"] == "upstream answered 503"
    assert node_failed["duration_ms"] == 2000
    # The downstream node never ran: cancelled by the terminal cascade,
    # observed once, without a duration.
    cancelled = by_event["node.cancelled"]
    assert cancelled["node_id"] == b and "duration_ms" not in cancelled
    failed = by_event["execution.failed"]
    assert failed["level"] == "WARNING"
    assert failed["error_node_id"] == a
    assert failed["error_message"] == "upstream answered 503"
    assert failed["duration_ms"] == 2000

    assert sample(metrics, "akasha_executions_finished_total", status="failed") == 1
    assert (
        sample(
            metrics,
            "akasha_node_execution_errors_total",
            node_type="test.source",
            error_kind="transient",
        )
        == 1
    )
    assert (
        sample(
            metrics,
            "akasha_node_executions_finished_total",
            node_type="test.sink",
            status="cancelled",
        )
        == 1
    )


def test_cancelled_before_start_has_no_duration() -> None:
    metrics = EngineMetrics()
    execution, _, _ = two_nodes()
    execution.pull_events()
    execution.cancel(reason="password=hunter2", now=at(1))
    with capture_json_logs() as lines:
        observe_execution(execution, execution.pull_events(), metrics)

    (done,) = [ln for ln in lines if ln["event"] == "execution.cancelled"]
    assert "duration_ms" not in done
    assert "hunter2" not in str(lines)  # the free-text reason is never logged
    assert sample(metrics, "akasha_executions_finished_total", status="cancelled") == 1
    assert (
        sample(metrics, "akasha_execution_duration_seconds_count", status="cancelled")
        is None
    )


def test_metric_labels_are_bounded_and_carry_no_ids_or_text() -> None:
    metrics = EngineMetrics()
    execution, a, _ = two_nodes()
    execution.start(now=at(1))
    execution.mark_node_ready(a)
    execution.start_node(a, now=at(1))
    execution.fail_node(
        a,
        ExecutionError(
            code="BOOM",
            message="api_key=sk-canary-0123456789abcdef",
            kind=NodeErrorKind.PERMANENT,
            node_id=a,
        ),
        now=at(2),
    )
    observe_execution(execution, execution.pull_events(), metrics)

    exposition = metrics.render().decode()
    for leaked in (execution.id, a, "corr-9", "sk-canary", "BOOM", "api_key"):
        assert leaked not in exposition
    label_names = {
        name
        for family in metrics.registry.collect()
        for s in family.samples
        for name in s.labels
    }
    assert label_names <= {"trigger_type", "status", "node_type", "error_kind", "le"}
