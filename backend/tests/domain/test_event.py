"""Event model and the events the Execution aggregate records (Phase 11)."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any

import pytest

from akasha.domain.event import PAYLOAD_KEYS, Event, EventType
from akasha.domain.execution import (
    Execution,
    ExecutionError,
    ExecutionMetadata,
    ExecutionStatus,
)
from akasha.domain.graph import Edge, Graph, NodeInstance
from akasha.domain.node import NodeErrorKind
from akasha.domain.primitives import new_id
from akasha.domain.trigger import TriggerType
from akasha.domain.workflow import WorkflowDraft
from akasha.errors import DomainValidationError, InvalidStateTransitionError

T0 = datetime(2100, 1, 1, tzinfo=UTC)
WORKFLOW = new_id("wf")
EXEC = new_id("exec")
NODE = new_id("node")


def at(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


def event(kind: EventType, **overrides: Any) -> Event:
    scope = kind.value.split(".")[0]
    fields: dict[str, Any] = {
        "event_type": kind,
        "occurred_at": T0,
        "workflow_id": WORKFLOW,
        "execution_id": EXEC if scope in ("execution", "node") else None,
        "node_id": NODE if scope == "node" else None,
        "payload": dict.fromkeys(PAYLOAD_KEYS[kind], "x"),
    }
    return Event(**(fields | overrides))


# -- Event ------------------------------------------------------------------


@pytest.mark.parametrize("event_type", list(EventType))
def test_every_event_type_is_constructible_with_its_schema(
    event_type: EventType,
) -> None:
    e = event(event_type)
    assert e.event_type is event_type
    assert e.event_id.startswith("evt_") and e.position is None
    assert e.schema_version == 1


def test_event_type_is_coerced_from_its_wire_value() -> None:
    e = event(EventType.NODE_SKIPPED, event_type="node.skipped")
    assert e.event_type is EventType.NODE_SKIPPED


def test_event_ids_are_unique() -> None:
    assert (
        event(EventType.NODE_SKIPPED).event_id != event(EventType.NODE_SKIPPED).event_id
    )


def test_event_is_immutable_and_copies_its_payload() -> None:
    payload: dict[str, Any] = {"attempt": 1}
    e = event(EventType.NODE_STARTED, payload=payload)
    payload["attempt"] = 99
    assert e.payload == {"attempt": 1}
    with pytest.raises(FrozenInstanceError):
        e.node_id = None  # type: ignore[misc]  # proving immutability


@pytest.mark.parametrize(
    ("event_type", "overrides"),
    [
        # execution/node events need an execution; workflow events must not
        (EventType.EXECUTION_STARTED, {"execution_id": None}),
        (EventType.WORKFLOW_PUBLISHED, {"execution_id": EXEC}),
        # node events need a node; nothing else may carry one
        (EventType.NODE_SKIPPED, {"node_id": None}),
        (EventType.EXECUTION_STARTED, {"node_id": NODE}),
        (EventType.WORKFLOW_PUBLISHED, {"node_id": NODE}),
    ],
)
def test_scope_ids_must_match_the_event_type(
    event_type: EventType, overrides: dict[str, Any]
) -> None:
    with pytest.raises(DomainValidationError):
        event(event_type, **overrides)


@pytest.mark.parametrize(
    "payload",
    [
        {},  # missing keys
        {"attempt": 1, "outputs": "leak"},  # unknown key: closed schema
        {"attempt": True},  # bool is not an int here
        {"attempt": 1.5},
        {"attempt": {"nested": "doc"}},
        {"attempt": ["x"]},
    ],
)
def test_payload_is_closed_and_scalar(payload: dict[str, Any]) -> None:
    with pytest.raises(DomainValidationError):
        event(EventType.NODE_STARTED, payload=payload)


@pytest.mark.parametrize(
    "overrides",
    [
        {"occurred_at": datetime(2100, 1, 1)},  # naive
        {"workflow_id": "not-an-id"},
        {"event_id": "evt-1"},
        {"node_id": "bad"},
        {"schema_version": 0},
        {"event_type": "execution.exploded"},
    ],
)
def test_malformed_events_are_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises((DomainValidationError, ValueError)):
        event(EventType.NODE_SKIPPED, **overrides)


# -- events recorded by Execution transitions --------------------------------


def chain(*labels: str) -> tuple[Execution, dict[str, str]]:
    """A linear graph ``labels[0] → labels[1] → …`` in a CREATED execution."""
    graph = Graph()
    ids: dict[str, str] = {}
    for label in labels:
        instance = NodeInstance.create("test.node", label=label)
        graph.add_node(instance)
        ids[label] = instance.id
    for a, b in pairwise(labels):
        graph.add_edge(Edge(ids[a], "out", ids[b], "in"))
    version = WorkflowDraft(graph=graph).publish(version=3)
    execution = Execution.create(
        WORKFLOW,
        version,
        metadata=ExecutionMetadata(
            trigger_type=TriggerType.API, correlation_id="corr-42"
        ),
        now=T0,
    )
    return execution, ids


def kinds(events: list[Event]) -> list[tuple[str, str | None]]:
    return [(e.event_type.value, e.node_id) for e in events]


def test_create_records_execution_created_with_context() -> None:
    execution, _ = chain("A")
    (created,) = execution.pull_events()
    assert created.event_type is EventType.EXECUTION_CREATED
    assert created.occurred_at == T0
    assert created.execution_id == execution.id
    assert created.workflow_id == WORKFLOW and created.workflow_version == 3
    assert created.correlation_id == "corr-42"
    assert created.payload == {"trigger_type": "api", "trigger_id": None}


def test_successful_lifecycle_records_every_transition_in_order() -> None:
    execution, ids = chain("A", "B")
    execution.enqueue()
    execution.start(now=at(1))
    execution.mark_node_ready(ids["A"])
    execution.start_node(ids["A"], now=at(2))
    execution.complete_node(ids["A"], outputs={"out": "secret"}, now=at(3))
    execution.skip_node(ids["B"], now=at(4))
    execution.complete(now=at(5))

    events = execution.pull_events()
    assert kinds(events) == [
        ("execution.created", None),
        ("execution.started", None),
        ("node.started", ids["A"]),
        ("node.completed", ids["A"]),
        ("node.skipped", ids["B"]),
        ("execution.completed", None),
    ]
    assert [e.occurred_at for e in events] == [T0, *map(at, range(1, 6))]
    assert events[2].payload == events[3].payload == {"attempt": 1}
    assert {e.correlation_id for e in events} == {"corr-42"}
    # Outputs never reach an event.
    assert "secret" not in repr(events)


def test_failure_records_node_failed_cascade_and_execution_failed() -> None:
    execution, ids = chain("A", "B", "C")
    execution.enqueue()
    execution.start(now=at(1))
    execution.mark_node_ready(ids["A"])
    execution.start_node(ids["A"], now=at(2))
    error = ExecutionError(
        code="BOOM",
        message="contains customer data",
        kind=NodeErrorKind.PERMANENT,
        node_id=ids["A"],
        details={"body": "customer data"},
    )
    execution.fail_node(ids["A"], error, now=at(3))
    execution.fail(error, now=at(4))

    events = execution.pull_events()
    # Cascade cancellations come in node-id order, then the terminal event.
    downstream = sorted([ids["B"], ids["C"]])
    assert kinds(events)[2:] == [
        ("node.started", ids["A"]),
        ("node.failed", ids["A"]),
        ("node.cancelled", downstream[0]),
        ("node.cancelled", downstream[1]),
        ("execution.failed", None),
    ]
    assert events[3].payload == {
        "attempt": 1,
        "error_code": "BOOM",
        "error_kind": "permanent",
    }
    assert events[-1].payload == {
        "error_code": "BOOM",
        "error_kind": "permanent",
        "error_node_id": ids["A"],
    }
    # Error messages and details (free text) never reach an event.
    assert "customer data" not in repr(events)


def test_cancel_records_running_node_cancel_without_reason() -> None:
    execution, ids = chain("A")
    execution.enqueue()
    execution.start(now=at(1))
    execution.mark_node_ready(ids["A"])
    execution.start_node(ids["A"], now=at(2))
    execution.pull_events()

    execution.cancel(reason="password=hunter2", now=at(3))

    events = execution.pull_events()
    assert kinds(events) == [
        ("node.cancelled", ids["A"]),
        ("execution.cancelled", None),
    ]
    assert events[-1].payload == {}
    assert "hunter2" not in repr(events)


def test_time_out_records_timed_out() -> None:
    execution, _ = chain("A")
    execution.enqueue()
    execution.start(now=at(1))
    execution.pull_events()
    execution.time_out(now=at(9))
    (_cancelled, timed_out) = execution.pull_events()
    assert timed_out.event_type is EventType.EXECUTION_TIMED_OUT
    assert timed_out.payload == {"error_code": "EXECUTION_TIMEOUT"}
    assert timed_out.occurred_at == at(9)


def test_illegal_transition_records_nothing() -> None:
    execution, _ = chain("A")
    execution.pull_events()
    with pytest.raises(InvalidStateTransitionError):
        execution.complete(now=at(1))
    assert execution.pending_events == ()
    assert execution.status is ExecutionStatus.CREATED


def test_pull_hands_over_once_and_pending_is_a_snapshot() -> None:
    execution, _ = chain("A")
    pending = execution.pending_events
    execution.enqueue()
    execution.start(now=at(1))
    assert len(pending) == 1  # a snapshot, not a live view
    assert len(execution.pull_events()) == 2
    assert execution.pull_events() == []


def test_rehydrated_execution_records_nothing() -> None:
    execution, _ = chain("A")
    rehydrated = Execution(
        id=execution.id,
        workflow_id=execution.workflow_id,
        workflow_version=execution.workflow_version,
        node_executions=execution.node_executions,
    )
    assert rehydrated.pending_events == ()
