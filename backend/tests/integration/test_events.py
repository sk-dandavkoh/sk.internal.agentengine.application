"""Event system against real PostgreSQL (roadmap Phase 11).

Covers the event store (serialization, idempotency, ordering, filtering),
the events the real runtime produces, and the core guarantee: events are
history next to the canonical state — a broken consumer never changes the
state, and events commit atomically with the state they describe.
"""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from akasha.application.events import EventPublisher
from akasha.application.executions import ExecutionService
from akasha.application.workflows import WorkflowService, WorkflowValidationFailedError
from akasha.domain.event import Event, EventType
from akasha.domain.execution import ExecutionStatus, NodeExecutionStatus
from akasha.domain.graph import Graph
from akasha.domain.node import (
    Node,
    NodeCapabilities,
    NodeDefinition,
    NodeExecutionContext,
    NodeMetadata,
    NodeOutput,
    NodeResult,
    PortType,
)
from akasha.domain.primitives import new_id
from akasha.domain.workflow import WorkflowDraft
from akasha.infrastructure.database import Database
from akasha.infrastructure.persistence import SqlAlchemyUnitOfWork
from akasha.runtime.plan import NodeCatalog
from akasha.runtime.runtime import ExecutionRuntime
from tests.integration.test_runtime import (
    GUARD,
    Harness,
    Probe,
    base_catalog,
    build,
    gated,
    never_set,
    wait_for,
)

pytestmark = pytest.mark.usefixtures("clean_db")

_E = ExecutionStatus
_N = NodeExecutionStatus
T0 = datetime(2100, 1, 1, tzinfo=UTC)


class Recorder:
    def __init__(self) -> None:
        self.seen: list[Event] = []

    async def __call__(self, event: Event) -> None:
        self.seen.append(event)


@dataclass
class EventHarness(Harness):
    publisher: EventPublisher = field(default_factory=EventPublisher)

    @property
    def executions(self) -> ExecutionService:
        return ExecutionService(self.uow, self.publisher)

    def runtime(self, catalog: NodeCatalog, **kwargs: Any) -> ExecutionRuntime:
        return super().runtime(catalog, events=self.publisher, **kwargs)

    async def stored(self, **filters: Any) -> Sequence[Event]:
        async with self.uow() as uow:
            return await uow.events.list(limit=10_000, **filters)


@pytest.fixture
def eh(database: Database) -> EventHarness:
    return EventHarness(database)


def trail(events: Sequence[Event], ids: Mapping[str, str]) -> list[str]:
    """Readable event trail: ``execution.started``, ``node.completed:A``."""
    label = {v: k for k, v in ids.items()}
    return [
        e.event_type.value + ("" if e.node_id is None else f":{label[e.node_id]}")
        for e in events
    ]


def evt(event_type: EventType, workflow_id: str, **kw: Any) -> Event:
    return Event(event_type=event_type, occurred_at=T0, workflow_id=workflow_id, **kw)


# -- event store -----------------------------------------------------------------


async def test_events_round_trip_through_the_store(eh: EventHarness) -> None:
    workflow_id, execution_id, node_id = new_id("wf"), new_id("exec"), new_id("node")
    original = Event(
        event_type=EventType.NODE_FAILED,
        occurred_at=T0,
        workflow_id=workflow_id,
        execution_id=execution_id,
        node_id=node_id,
        workflow_version=4,
        correlation_id="corr-rt",
        payload={"attempt": 2, "error_code": "BOOM", "error_kind": "permanent"},
    )
    async with eh.uow() as uow:
        await uow.events.append([original])
        await uow.commit()

    (loaded,) = await eh.stored(execution_id=execution_id)

    assert loaded.position is not None and loaded.position > 0
    assert replace(loaded, position=None) == original
    assert loaded.occurred_at.tzinfo is not None


async def test_append_is_idempotent_on_event_id(eh: EventHarness) -> None:
    workflow_id = new_id("wf")
    event = evt(EventType.WORKFLOW_PUBLISHED, workflow_id, workflow_version=1)
    async with eh.uow() as uow:
        await uow.events.append([event])
        await uow.commit()
    async with eh.uow() as uow:  # a redelivered/retried append
        await uow.events.append([event, event])
        await uow.commit()

    stored = await eh.stored(workflow_id=workflow_id)
    assert [e.event_id for e in stored] == [event.event_id]


async def test_events_are_ordered_by_position_and_paged_by_cursor(
    eh: EventHarness,
) -> None:
    workflow_id, other = new_id("wf"), new_id("wf")
    ours = [
        evt(EventType.WORKFLOW_PUBLISHED, workflow_id, workflow_version=v)
        for v in (1, 2, 3)
    ]
    async with eh.uow() as uow:
        await uow.events.append([ours[0], evt(EventType.WORKFLOW_PUBLISHED, other)])
        await uow.events.append(ours[1:])
        await uow.commit()

    stored = await eh.stored(workflow_id=workflow_id)
    assert [e.event_id for e in stored] == [e.event_id for e in ours]
    positions = [e.position or 0 for e in stored]
    assert positions == sorted(positions)

    async with eh.uow() as uow:
        first = await uow.events.list(workflow_id=workflow_id, limit=1)
        rest = await uow.events.list(
            workflow_id=workflow_id, after=first[0].position or 0, limit=10
        )
    assert [e.event_id for e in [*first, *rest]] == [e.event_id for e in ours]


async def test_uncommitted_events_are_rolled_back(eh: EventHarness) -> None:
    workflow_id = new_id("wf")
    async with eh.uow() as uow:
        await uow.events.append([evt(EventType.WORKFLOW_PUBLISHED, workflow_id)])
        # no commit: leaving the unit of work rolls back
    assert await eh.stored(workflow_id=workflow_id) == []


async def test_positions_cannot_be_forged(database: Database, eh: EventHarness) -> None:
    workflow_id = new_id("wf")
    event = evt(EventType.WORKFLOW_PUBLISHED, workflow_id)
    async with eh.uow() as uow:
        await uow.events.append([event])
        await uow.commit()
    # The identity is GENERATED ALWAYS: nobody can forge an order position.
    async with database.engine.connect() as conn:
        with pytest.raises(DBAPIError, match="position"):
            await conn.execute(
                text(
                    "INSERT INTO events (position, event_id, event_type,"
                    " schema_version, occurred_at, workflow_id, payload)"
                    " VALUES (1, 'evt_x', 'workflow.published', 1, now(), :p, '{}')"
                ),
                {"p": workflow_id},
            )


# -- runtime lifecycle -----------------------------------------------------------


async def test_successful_run_reconstructs_from_events_plus_state(
    eh: EventHarness,
) -> None:
    live = Recorder()
    eh.publisher.subscribe(live)
    catalog = base_catalog(Probe())
    graph, ids = build(
        {"A": ("test.emit", {"value": "a"}), "B": ("test.append", {"suffix": "b"})},
        ["A.out>B.text"],
    )
    queued = await eh.queue(catalog, graph)
    await eh.runtime(catalog).run(queued.id)

    events = await eh.stored(execution_id=queued.id)
    assert trail(events, ids) == [
        "execution.created",
        "execution.started",
        "node.started:A",
        "node.completed:A",
        "node.started:B",
        "node.completed:B",
        "execution.completed",
    ]
    # Live subscribers saw exactly the persisted history, in the same order.
    assert [e.event_id for e in live.seen] == [e.event_id for e in events]
    # Every event carries the execution's context and correlation id.
    assert {(e.workflow_id, e.workflow_version, e.correlation_id) for e in events} == {
        (queued.workflow_id, queued.workflow_version.version, "corr-1")
    }
    # Events and canonical state agree on what happened when.
    state = await eh.load(queued.id)
    by = {(e.event_type, e.node_id): e.occurred_at for e in events}
    assert by[(EventType.EXECUTION_STARTED, None)] == state.started_at
    assert by[(EventType.EXECUTION_COMPLETED, None)] == state.finished_at
    for node_id in ids.values():
        ne = state.node(node_id)
        assert by[(EventType.NODE_STARTED, node_id)] == ne.started_at
        assert by[(EventType.NODE_COMPLETED, node_id)] == ne.finished_at
    # Outputs are state, not events.
    assert all("out" not in e.payload for e in events)


async def test_failure_skip_and_cascade_events(eh: EventHarness) -> None:
    probe = Probe()
    catalog = base_catalog(probe)

    class Silent(Node):
        """Control flow that takes no branch: its consumers are skipped."""

        definition = NodeDefinition(
            metadata=NodeMetadata(
                node_id="test.silent", name="s", description="Silent.", version="1.0.0"
            ),
            outputs=(NodeOutput("out", PortType.STRING),),
            capabilities=NodeCapabilities(is_control_flow=True),
        )

        async def execute(self, context: NodeExecutionContext) -> NodeResult:
            return NodeResult({})

    catalog["test.silent"] = Silent
    graph, ids = build(
        {
            "Q": ("test.silent", {}),
            "S": ("test.append", {"suffix": "s"}),
            "A": ("test.emit", {"value": "a"}),
            "F": ("test.fail", {}),
            "C": ("test.append", {"suffix": "c"}),
        },
        ["Q.out>S.text", "A.out>F.text", "F.out>C.text"],
    )
    queued = await eh.queue(catalog, graph)
    await eh.runtime(catalog).run(queued.id)

    events = await eh.stored(execution_id=queued.id)
    got = trail(events, ids)
    assert got[0] == "execution.created" and got[-1] == "execution.failed"
    for before, after in [
        ("node.started:A", "node.completed:A"),
        ("node.completed:A", "node.started:F"),
        ("node.started:F", "node.failed:F"),
        ("node.completed:Q", "node.skipped:S"),
        ("node.failed:F", "node.cancelled:C"),
    ]:
        assert got.index(before) < got.index(after)
    failed = events[-1]
    assert failed.payload == {
        "error_code": "NODE_EXECUTION_ERROR",
        "error_kind": "permanent",
        "error_node_id": ids["F"],
    }
    state = await eh.load(queued.id)
    assert state.status is _E.FAILED
    assert state.node(ids["S"]).status is _N.SKIPPED


async def test_cancel_running_execution_events(eh: EventHarness) -> None:
    probe = Probe()
    catalog = base_catalog(probe)
    started = asyncio.Event()
    catalog["test.gate"] = gated("test.gate", probe, started, never_set())
    graph, ids = build(
        {"A": ("test.emit", {"value": "a"}), "B": ("test.gate", {})}, ["A.out>B.text"]
    )
    queued = await eh.queue(catalog, graph)
    runtime = eh.runtime(catalog)
    task = asyncio.create_task(runtime.run(queued.id))
    await wait_for(started)
    await runtime.cancel(queued.id, reason="secret-reason")
    async with asyncio.timeout(GUARD):
        await task

    events = await eh.stored(execution_id=queued.id)
    assert trail(events, ids)[-3:] == [
        "node.started:B",
        "node.cancelled:B",
        "execution.cancelled",
    ]
    assert all("secret-reason" not in repr(e) for e in events)


async def test_out_of_process_cancel_of_queued_execution_is_evented(
    eh: EventHarness,
) -> None:
    catalog = base_catalog(Probe())
    graph, ids = build(
        {"A": ("test.emit", {"value": "a"}), "B": ("test.append", {"suffix": "b"})},
        ["A.out>B.text"],
    )
    queued = await eh.queue(catalog, graph)

    await eh.runtime(catalog).cancel(queued.id)

    events = await eh.stored(execution_id=queued.id)
    got = trail(events, ids)
    assert got[0] == "execution.created" and got[-1] == "execution.cancelled"
    # The cascade covers every node (in node-id order, ids are random here).
    assert sorted(got[1:-1]) == ["node.cancelled:A", "node.cancelled:B"]


async def test_concurrent_executions_keep_per_execution_order(
    eh: EventHarness,
) -> None:
    catalog = base_catalog(Probe())
    graph, ids = build(
        {
            "A": ("test.emit", {"value": "a"}),
            "B": ("test.append", {"suffix": "b"}),
            "C": ("test.append", {"suffix": "c"}),
        },
        ["A.out>B.text", "B.out>C.text"],
    )
    workflow_id, version = await eh.publish(catalog, graph)
    queued = [
        await eh.executions.create_execution(workflow_id, version) for _ in range(6)
    ]
    runtime = eh.runtime(catalog)
    async with asyncio.timeout(GUARD):
        await asyncio.gather(*(runtime.run(q.id) for q in queued))

    for q in queued:
        events = await eh.stored(execution_id=q.id)
        assert trail(events, ids) == [
            "execution.created",
            "execution.started",
            "node.started:A",
            "node.completed:A",
            "node.started:B",
            "node.completed:B",
            "node.started:C",
            "node.completed:C",
            "execution.completed",
        ]


# -- broken consumers never change domain state ------------------------------------


async def test_broken_and_hanging_consumers_do_not_affect_the_run(
    database: Database,
) -> None:
    eh = EventHarness(database, publisher=EventPublisher(subscriber_timeout=0.05))
    calls: list[str] = []

    async def raises(event: Event) -> None:
        calls.append("raises")
        raise RuntimeError("consumer exploded")

    async def hangs(event: Event) -> None:
        if event.event_type is EventType.NODE_STARTED:
            await asyncio.Event().wait()

    healthy = Recorder()
    eh.publisher.subscribe(raises)
    eh.publisher.subscribe(hangs)
    eh.publisher.subscribe(healthy)
    catalog = base_catalog(Probe())
    graph, ids = build(
        {"A": ("test.emit", {"value": "a"}), "B": ("test.append", {"suffix": "b"})},
        ["A.out>B.text"],
    )
    queued = await eh.queue(catalog, graph)

    async with asyncio.timeout(GUARD):
        result = await eh.runtime(catalog).run(queued.id)

    # Domain state: correct and persisted, exactly as without consumers.
    state = await eh.load(queued.id)
    assert result.status is state.status is _E.COMPLETED
    assert state.node(ids["B"]).outputs == {"out": "ab"}
    # Event history: complete despite the consumers.
    events = await eh.stored(execution_id=queued.id)
    assert len(events) == 7
    assert len(calls) == 7
    assert [e.event_id for e in healthy.seen] == [e.event_id for e in events]


async def test_subscribers_only_see_committed_state(database: Database) -> None:
    """At delivery time the state the event describes is already durable:
    a fresh transaction sees it. (Were events delivered before commit, the
    read would block on the writer's row lock and time out — so every
    event must also be *observed*, not merely not contradicted.)"""
    eh = EventHarness(database, publisher=EventPublisher(subscriber_timeout=1))
    observed: list[tuple[EventType, str]] = []

    async def verify(event: Event) -> None:
        if event.execution_id is None:
            return
        async with eh.uow() as uow:
            state = await uow.executions.get(event.execution_id)
        assert state is not None
        subject = state if event.node_id is None else state.node(event.node_id)
        observed.append((event.event_type, subject.status.value))

    eh.publisher.subscribe(verify)
    catalog = base_catalog(Probe())
    graph, _ = build(
        {"A": ("test.emit", {"value": "a"}), "B": ("test.append", {"suffix": "b"})},
        ["A.out>B.text"],
    )
    queued = await eh.queue(catalog, graph)
    await eh.runtime(catalog).run(queued.id)

    T = EventType
    assert observed == [
        (T.EXECUTION_CREATED, "queued"),
        (T.EXECUTION_STARTED, "running"),
        (T.NODE_STARTED, "running"),
        # A's completion is saved together with B becoming ready.
        (T.NODE_COMPLETED, "completed"),
        (T.NODE_STARTED, "running"),
        (T.NODE_COMPLETED, "completed"),
        (T.EXECUTION_COMPLETED, "completed"),
    ]


class _FailingEvents:
    async def append(self, events: Sequence[Event]) -> None:
        raise RuntimeError("event store unavailable")


async def test_event_store_failure_rolls_back_the_state_change(
    eh: EventHarness, database: Database
) -> None:
    """No state without its events: a failed append aborts the transaction."""
    live = Recorder()
    eh.publisher.subscribe(live)
    catalog = base_catalog(Probe())
    graph, _ = build(
        {"A": ("test.emit", {"value": "a"}), "B": ("test.append", {"suffix": "b"})},
        ["A.out>B.text"],
    )
    workflow_id, version = await eh.publish(catalog, graph)

    def broken_uow() -> SqlAlchemyUnitOfWork:
        uow = SqlAlchemyUnitOfWork(database)
        uow.events = _FailingEvents()  # type: ignore[assignment]  # partial fake
        return uow

    with pytest.raises(RuntimeError, match="event store unavailable"):
        await ExecutionService(broken_uow, eh.publisher).create_execution(
            workflow_id, version
        )

    async with database.engine.connect() as conn:
        count = (await conn.execute(text("SELECT count(*) FROM executions"))).scalar()
    assert count == 0
    assert live.seen == []  # nothing published for a rolled-back change
    assert [e.event_type for e in await eh.stored()] == [EventType.WORKFLOW_PUBLISHED]


# -- workflow events ------------------------------------------------------------------


async def test_publish_records_workflow_published(eh: EventHarness) -> None:
    live = Recorder()
    eh.publisher.subscribe(live, event_types=[EventType.WORKFLOW_PUBLISHED])
    catalog = base_catalog(Probe())
    service = WorkflowService(
        eh.uow, {t: c.definition for t, c in catalog.items()}, eh.publisher
    )
    workflow = await service.create_workflow("p")
    graph: Graph = build(
        {"A": ("test.emit", {"value": "a"}), "B": ("test.append", {"suffix": "b"})},
        ["A.out>B.text"],
    )[0]
    await service.update_draft(
        workflow.id,
        expected_revision=workflow.revision,
        name="p",
        draft=WorkflowDraft(graph=graph),
    )
    v1 = await service.publish_workflow(workflow.id)

    (event,) = await eh.stored(workflow_id=workflow.id)
    assert event.event_type is EventType.WORKFLOW_PUBLISHED
    assert (event.workflow_version, event.execution_id) == (1, None)
    assert event.occurred_at == v1.published_at
    assert [e.event_id for e in live.seen] == [event.event_id]


async def test_failed_publish_records_no_event(eh: EventHarness) -> None:
    catalog = base_catalog(Probe())
    service = WorkflowService(eh.uow, {t: c.definition for t, c in catalog.items()})
    workflow = await service.create_workflow("p")
    graph = build({"B": ("test.append", {"suffix": "b"})}, [])[0]  # missing input
    await service.update_draft(
        workflow.id,
        expected_revision=workflow.revision,
        name="p",
        draft=WorkflowDraft(graph=graph),
    )
    with pytest.raises(WorkflowValidationFailedError):
        await service.publish_workflow(workflow.id)
    assert await eh.stored(workflow_id=workflow.id) == []
