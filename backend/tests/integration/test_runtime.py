"""ExecutionRuntime against real PostgreSQL (roadmap Phase 08).

Every scenario runs published Project Versions through the real runtime,
real Unit of Work and a real database, then asserts on the *persisted*
state. Test nodes are deterministic; synchronization uses asyncio events
and barriers, never sleeps. The only wall-clock waits are the timeout
scenarios, where a node blocks forever and the configured limit (not test
timing) decides the outcome.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy import text

from akasha.application.executions import ExecutionNotFoundError, ExecutionService
from akasha.application.persistence import ConcurrentModificationError, UnitOfWork
from akasha.application.projects import ProjectService, ProjectVersionNotFoundError
from akasha.domain.execution import (
    Execution,
    ExecutionMetadata,
    ExecutionStatus,
    NodeExecutionStatus,
    RetryPolicy,
    TimeoutPolicy,
)
from akasha.domain.graph import Edge, Graph, NodeInstance
from akasha.domain.node import (
    Node,
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
from akasha.domain.project import ProjectDraft
from akasha.errors import ConfigurationError, InvalidStateTransitionError
from akasha.infrastructure.database import Database
from akasha.infrastructure.persistence import SqlAlchemyUnitOfWork
from akasha.runtime.plan import NodeCatalog
from akasha.runtime.runtime import ExecutionAlreadyRunningError, ExecutionRuntime

pytestmark = pytest.mark.usefixtures("clean_db")

S = PortType.STRING
_E = ExecutionStatus
_N = NodeExecutionStatus
Run = Callable[[NodeExecutionContext], Awaitable[Mapping[str, Any]]]

# Hard upper bound for a scenario: a deadlock fails the test instead of
# hanging CI. Never used to *wait* for a state.
GUARD = 10


# -- test nodes ------------------------------------------------------------


def node_type(
    type_id: str,
    run: Run,
    *,
    inputs: tuple[NodeInput, ...] = (),
    outputs: tuple[NodeOutput, ...] = (NodeOutput("out", S),),
) -> type[Node]:
    definition_ = NodeDefinition(
        metadata=NodeMetadata(
            node_id=type_id, name=type_id, description="Test node.", version="1.0.0"
        ),
        inputs=inputs,
        outputs=outputs,
    )

    class TestNode(Node):
        definition = definition_

        async def execute(self, context: NodeExecutionContext) -> NodeResult:
            return NodeResult(await run(context))

    return TestNode


@dataclass
class Probe:
    """Records what node code actually did (the runtime's side effects)."""

    started: list[str] = field(default_factory=list)
    cleaned: list[str] = field(default_factory=list)
    contexts: dict[str, NodeExecutionContext] = field(default_factory=dict)

    def enter(self, ctx: NodeExecutionContext) -> None:
        self.started.append(ctx.node_instance_id)
        self.contexts[ctx.node_instance_id] = ctx


def text_in(name: str = "text") -> tuple[NodeInput, ...]:
    return (NodeInput(name, S),)


def base_catalog(probe: Probe) -> dict[str, type[Node]]:
    async def emit(ctx: NodeExecutionContext) -> Mapping[str, Any]:
        probe.enter(ctx)
        return {"out": ctx.config["value"]}

    async def append(ctx: NodeExecutionContext) -> Mapping[str, Any]:
        probe.enter(ctx)
        return {"out": ctx.inputs["text"] + ctx.config["suffix"]}

    async def join(ctx: NodeExecutionContext) -> Mapping[str, Any]:
        probe.enter(ctx)
        return {"out": ctx.inputs["sep"].join(ctx.inputs["items"])}

    async def fail(ctx: NodeExecutionContext) -> Mapping[str, Any]:
        probe.enter(ctx)
        raise NodeError("upstream said no", kind=NodeErrorKind.PERMANENT)

    return {
        "test.emit": node_type("test.emit", emit),
        "test.append": node_type("test.append", append, inputs=text_in()),
        "test.join": node_type(
            "test.join",
            join,
            inputs=(
                NodeInput("items", S, multiple=True),
                NodeInput("sep", S, required=False, default="+"),
            ),
        ),
        "test.fail": node_type("test.fail", fail, inputs=text_in()),
    }


def gated(
    type_id: str,
    probe: Probe,
    started: asyncio.Event,
    release: asyncio.Event,
    *,
    inputs: tuple[NodeInput, ...] = text_in(),
) -> type[Node]:
    """Signals ``started``, blocks until ``release``, records cleanup."""

    async def run(ctx: NodeExecutionContext) -> Mapping[str, Any]:
        probe.enter(ctx)
        started.set()
        try:
            await release.wait()
        finally:
            probe.cleaned.append(f"{ctx.node_instance_id}:{ctx.is_cancelled()}")
        return {"out": ctx.inputs.get("text", "") + "!"}

    return node_type(type_id, run, inputs=inputs)


def never_set() -> asyncio.Event:
    return asyncio.Event()


# -- graph helpers -------------------------------------------------------------


def build(
    nodes: dict[str, tuple[str, dict[str, Any]]], edges: list[str]
) -> tuple[Graph, dict[str, str]]:
    """``nodes``: label -> (type, config); ``edges``: "A.out>B.text"."""
    graph = Graph()
    ids: dict[str, str] = {}
    for label, (type_id, config) in nodes.items():
        instance = NodeInstance.create(type_id, config=config, label=label)
        graph.add_node(instance)
        ids[label] = instance.id
    for spec in edges:
        src, dst = spec.split(">")
        (s, sp), (t, tp) = src.split("."), dst.split(".")
        graph.add_edge(Edge(ids[s], sp, ids[t], tp))
    return graph, ids


class TickClock:
    """Strictly increasing timestamps: timestamp order == event order."""

    def __init__(self) -> None:
        self.now = datetime(2100, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


@dataclass
class Harness:
    database: Database
    clock: TickClock = field(default_factory=TickClock)

    def uow(self) -> UnitOfWork:
        return SqlAlchemyUnitOfWork(self.database)

    @property
    def executions(self) -> ExecutionService:
        return ExecutionService(self.uow)

    async def publish(self, catalog: NodeCatalog, graph: Graph) -> tuple[str, int]:
        svc = ProjectService(self.uow, {t: c.definition for t, c in catalog.items()})
        project = await svc.create_project("demo")
        await svc.update_draft(
            project.id,
            expected_revision=project.revision,
            name="demo",
            draft=ProjectDraft(graph=graph),
        )
        version = await svc.publish_project(project.id)
        return project.id, version.version

    async def queue(
        self,
        catalog: NodeCatalog,
        graph: Graph,
        *,
        timeout_policy: TimeoutPolicy | None = None,
    ) -> Execution:
        project_id, version = await self.publish(catalog, graph)
        return await self.executions.create_execution(
            project_id,
            version,
            metadata=ExecutionMetadata(correlation_id="corr-1"),
            timeout_policy=timeout_policy,
        )

    def runtime(self, catalog: NodeCatalog, **kwargs: Any) -> ExecutionRuntime:
        return ExecutionRuntime(self.uow, catalog, clock=self.clock, **kwargs)

    async def load(self, execution_id: str) -> Execution:
        return await self.executions.get_execution(execution_id)


@pytest.fixture
def h(database: Database) -> Harness:
    return Harness(database)


def statuses(execution: Execution, ids: dict[str, str]) -> dict[str, _N]:
    return {label: execution.node(nid).status for label, nid in ids.items()}


def t(value: datetime | None) -> datetime:
    assert value is not None
    return value


async def wait_for(event: asyncio.Event) -> None:
    async with asyncio.timeout(GUARD):
        await event.wait()


# -- successful graphs -----------------------------------------------------------


async def test_linear_graph_propagates_outputs_in_dependency_order(
    h: Harness,
) -> None:
    probe = Probe()
    catalog = base_catalog(probe)
    graph, ids = build(
        {
            "A": ("test.emit", {"value": "a"}),
            "B": ("test.append", {"suffix": "b"}),
            "C": ("test.append", {"suffix": "c"}),
        },
        ["A.out>B.text", "B.out>C.text"],
    )
    queued = await h.queue(catalog, graph)

    result = await h.runtime(catalog).run(queued.id)

    assert result.status is _E.COMPLETED
    assert probe.started == [ids["A"], ids["B"], ids["C"]]
    loaded = await h.load(queued.id)
    assert loaded.status is _E.COMPLETED and loaded.error is None
    assert loaded.node(ids["C"]).outputs == {"out": "abc"}
    assert loaded.node(ids["B"]).outputs == {"out": "ab"}
    a, b, c = (loaded.node(ids[x]) for x in "ABC")
    assert all(ne.attempt == 1 and ne.failures == [] for ne in (a, b, c))
    # TickClock: strictly increasing timestamps prove the ordering.
    assert t(a.finished_at) < t(b.started_at) < t(b.finished_at) < t(c.started_at)
    assert t(loaded.started_at) < t(a.started_at)
    assert t(c.finished_at) < t(loaded.finished_at)


async def test_branching_feeds_both_branches_from_one_output(h: Harness) -> None:
    catalog = base_catalog(Probe())
    graph, ids = build(
        {
            "A": ("test.emit", {"value": "a"}),
            "B": ("test.append", {"suffix": "-b"}),
            "C": ("test.append", {"suffix": "-c"}),
        },
        ["A.out>B.text", "A.out>C.text"],
    )
    queued = await h.queue(catalog, graph)
    await h.runtime(catalog).run(queued.id)

    loaded = await h.load(queued.id)
    assert loaded.status is _E.COMPLETED
    assert loaded.node(ids["B"]).outputs == {"out": "a-b"}
    assert loaded.node(ids["C"]).outputs == {"out": "a-c"}


async def test_roadmap_branch_then_chain_a_b_c_d(h: Harness) -> None:
    """A → B, A → C → D."""
    probe = Probe()
    catalog = base_catalog(probe)
    graph, ids = build(
        {
            "A": ("test.emit", {"value": "a"}),
            "B": ("test.append", {"suffix": "b"}),
            "C": ("test.append", {"suffix": "c"}),
            "D": ("test.append", {"suffix": "d"}),
        },
        ["A.out>B.text", "A.out>C.text", "C.out>D.text"],
    )
    queued = await h.queue(catalog, graph)
    await h.runtime(catalog).run(queued.id)

    loaded = await h.load(queued.id)
    assert loaded.status is _E.COMPLETED
    assert loaded.node(ids["B"]).outputs == {"out": "ab"}
    assert loaded.node(ids["D"]).outputs == {"out": "acd"}
    assert probe.started[0] == ids["A"]
    assert probe.started.index(ids["C"]) < probe.started.index(ids["D"])


async def test_merge_waits_for_every_dependency_and_collects_in_edge_order(
    h: Harness,
) -> None:
    """Diamond A → {B, C} → D. B is forced to finish *after* C; D must still
    start only after both and collect values in edge order, not completion
    order."""
    probe = Probe()
    catalog = base_catalog(probe)
    c_done = asyncio.Event()

    async def late(ctx: NodeExecutionContext) -> Mapping[str, Any]:
        probe.enter(ctx)
        await c_done.wait()
        return {"out": ctx.inputs["text"] + "-b"}

    async def early(ctx: NodeExecutionContext) -> Mapping[str, Any]:
        probe.enter(ctx)
        c_done.set()
        return {"out": ctx.inputs["text"] + "-c"}

    catalog["test.late"] = node_type("test.late", late, inputs=text_in())
    catalog["test.early"] = node_type("test.early", early, inputs=text_in())
    graph, ids = build(
        {
            "A": ("test.emit", {"value": "a"}),
            "B": ("test.late", {}),
            "C": ("test.early", {}),
            "D": ("test.join", {}),
        },
        ["A.out>B.text", "A.out>C.text", "B.out>D.items", "C.out>D.items"],
    )
    queued = await h.queue(catalog, graph)
    async with asyncio.timeout(GUARD):
        await h.runtime(catalog).run(queued.id)

    loaded = await h.load(queued.id)
    b, c, d = (loaded.node(ids[x]) for x in "BCD")
    assert d.outputs == {"out": "a-b+a-c"}  # edge order; default sep "+"
    # B and C may be *recorded* in one batch (node-id order); what matters
    # is that D started after both were recorded.
    assert max(t(b.finished_at), t(c.finished_at)) < t(d.started_at)
    assert probe.started.count(ids["D"]) == 1


async def test_parallel_branches_really_run_concurrently(h: Harness) -> None:
    """Each branch waits at a 2-party barrier: sequential execution would
    deadlock (caught by the guard), so completion proves parallelism."""
    catalog = base_catalog(Probe())
    barrier = asyncio.Barrier(2)

    async def meet(ctx: NodeExecutionContext) -> Mapping[str, Any]:
        async with asyncio.timeout(GUARD):
            await barrier.wait()
        return {"out": ctx.inputs["text"]}

    catalog["test.meet"] = node_type("test.meet", meet, inputs=text_in())
    graph, _ = build(
        {
            "A": ("test.emit", {"value": "a"}),
            "B": ("test.meet", {}),
            "C": ("test.meet", {}),
        },
        ["A.out>B.text", "A.out>C.text"],
    )
    queued = await h.queue(catalog, graph)
    result = await h.runtime(catalog).run(queued.id)
    assert result.status is _E.COMPLETED


async def test_concurrency_limit_bounds_in_flight_nodes(h: Harness) -> None:
    probe = Probe()
    catalog = base_catalog(probe)
    two_started, release = asyncio.Event(), asyncio.Event()

    async def hold(ctx: NodeExecutionContext) -> Mapping[str, Any]:
        probe.enter(ctx)
        if len(probe.started) == 3:  # A plus two branches
            two_started.set()
        await release.wait()
        return {"out": ctx.inputs["text"]}

    catalog["test.hold"] = node_type("test.hold", hold, inputs=text_in())
    graph, ids = build(
        {
            "A": ("test.emit", {"value": "a"}),
            "B": ("test.hold", {}),
            "C": ("test.hold", {}),
            "D": ("test.hold", {}),
        },
        ["A.out>B.text", "A.out>C.text", "A.out>D.text"],
    )
    queued = await h.queue(catalog, graph)
    task = asyncio.create_task(h.runtime(catalog, max_concurrency=2).run(queued.id))
    await wait_for(two_started)

    branches = sorted(ids[x] for x in "BCD")
    assert probe.started[1:] == branches[:2]  # scheduler: id order, 2 slots
    snapshot = await h.load(queued.id)
    assert [snapshot.node(n).status for n in branches] == [
        _N.RUNNING,
        _N.RUNNING,
        _N.READY,
    ]

    release.set()
    async with asyncio.timeout(GUARD):
        result = await task
    assert result.status is _E.COMPLETED
    assert probe.started[1:] == branches


# -- failures ----------------------------------------------------------------------


async def test_node_failure_fails_execution_and_cancels_downstream(
    h: Harness,
) -> None:
    probe = Probe()
    catalog = base_catalog(probe)
    graph, ids = build(
        {
            "A": ("test.emit", {"value": "a"}),
            "B": ("test.fail", {}),
            "C": ("test.append", {"suffix": "c"}),
        },
        ["A.out>B.text", "B.out>C.text"],
    )
    queued = await h.queue(catalog, graph)
    await h.runtime(catalog).run(queued.id)

    loaded = await h.load(queued.id)
    assert loaded.status is _E.FAILED
    assert statuses(loaded, ids) == {
        "A": _N.COMPLETED,
        "B": _N.FAILED,
        "C": _N.CANCELLED,
    }
    error = loaded.node(ids["B"]).error
    assert error is not None
    assert (error.code, error.kind, error.node_id) == (
        "NODE_EXECUTION_ERROR",
        NodeErrorKind.PERMANENT,
        ids["B"],
    )
    assert loaded.error == error
    assert loaded.node(ids["C"]).attempt == 0 and ids["C"] not in probe.started


async def test_failure_stops_in_flight_sibling(h: Harness) -> None:
    probe = Probe()
    catalog = base_catalog(probe)
    c_started = asyncio.Event()
    catalog["test.gate"] = gated("test.gate", probe, c_started, never_set())

    async def fail_after_c(ctx: NodeExecutionContext) -> Mapping[str, Any]:
        await c_started.wait()
        raise NodeError("no", kind=NodeErrorKind.PERMANENT)

    catalog["test.late_fail"] = node_type(
        "test.late_fail", fail_after_c, inputs=text_in()
    )
    graph, ids = build(
        {
            "A": ("test.emit", {"value": "a"}),
            "B": ("test.late_fail", {}),
            "C": ("test.gate", {}),
            "D": ("test.join", {}),
        },
        ["A.out>B.text", "A.out>C.text", "B.out>D.items", "C.out>D.items"],
    )
    queued = await h.queue(catalog, graph)
    async with asyncio.timeout(GUARD):
        await h.runtime(catalog).run(queued.id)

    loaded = await h.load(queued.id)
    assert loaded.status is _E.FAILED and loaded.error is not None
    assert loaded.error.node_id == ids["B"]
    assert statuses(loaded, ids) == {
        "A": _N.COMPLETED,
        "B": _N.FAILED,
        "C": _N.CANCELLED,
        "D": _N.CANCELLED,
    }
    # C's cleanup really ran, and it could see that it was being stopped.
    assert probe.cleaned == [f"{ids['C']}:True"]


async def test_unexpected_exception_is_contained_and_never_leaks_its_message(
    h: Harness, database: Database, caplog: pytest.LogCaptureFixture
) -> None:
    canary = "sk-live-hunter2-do-not-leak"  # fake credential-looking text

    async def crash(ctx: NodeExecutionContext) -> Mapping[str, Any]:
        raise RuntimeError(f"connecting with api_key={canary}")

    catalog = base_catalog(Probe())
    catalog["test.crash"] = node_type("test.crash", crash, inputs=text_in())
    graph, ids = build(
        {"A": ("test.emit", {"value": "a"}), "B": ("test.crash", {})},
        ["A.out>B.text"],
    )
    queued = await h.queue(catalog, graph)
    with caplog.at_level(logging.DEBUG):
        await h.runtime(catalog).run(queued.id)

    loaded = await h.load(queued.id)
    error = loaded.node(ids["B"]).error
    assert loaded.status is _E.FAILED and error is not None
    assert error.code == "NODE_UNEXPECTED_ERROR"
    assert error.kind is NodeErrorKind.PERMANENT
    assert "RuntimeError" in error.message
    async with database.engine.connect() as conn:
        raw: list[str] = list(
            (
                await conn.execute(
                    text(
                        "SELECT e.error::text || n.failures::text FROM executions e"
                        " JOIN node_executions n ON n.execution_id = e.id"
                        " WHERE e.id = :id"
                    ),
                    {"id": queued.id},
                )
            ).scalars()
        )
    assert len(raw) == 2
    assert all(canary not in row for row in raw)
    logged = [str(v) for r in caplog.records for v in r.__dict__.values()]
    assert any("node_unexpected_error" in v for v in logged)
    assert not any(canary in v for v in logged)


async def test_invalid_node_output_is_a_structured_node_failure(h: Harness) -> None:
    async def wrong(ctx: NodeExecutionContext) -> Mapping[str, Any]:
        return {"out": 42}  # declared STRING

    catalog = base_catalog(Probe())
    catalog["test.wrong"] = node_type("test.wrong", wrong, inputs=text_in())
    graph, ids = build(
        {"A": ("test.emit", {"value": "a"}), "B": ("test.wrong", {})},
        ["A.out>B.text"],
    )
    queued = await h.queue(catalog, graph)
    await h.runtime(catalog).run(queued.id)

    error = (await h.load(queued.id)).node(ids["B"]).error
    assert error is not None and error.code == "NODE_OUTPUT_INVALID"
    assert error.kind is NodeErrorKind.VALIDATION


async def test_graph_invalid_at_runtime_fails_before_any_node_runs(
    h: Harness,
) -> None:
    """The version was valid at publish; the runtime catalog no longer knows
    one node type. The run must fail as a whole without starting a node."""
    probe = Probe()
    publish_catalog = base_catalog(probe)
    graph, ids = build(
        {"A": ("test.emit", {"value": "a"}), "B": ("test.append", {"suffix": "b"})},
        ["A.out>B.text"],
    )
    queued = await h.queue(publish_catalog, graph)
    runtime_catalog = {"test.emit": publish_catalog["test.emit"]}

    result = await h.runtime(runtime_catalog).run(queued.id)

    loaded = await h.load(queued.id)
    assert result.status is loaded.status is _E.FAILED
    assert loaded.error is not None
    assert loaded.error.code == "EXECUTION_GRAPH_INVALID"
    assert loaded.error.details == {"issue_codes": "unknown_node_type"}
    assert loaded.started_at is None and probe.started == []
    assert set(statuses(loaded, ids).values()) == {_N.CANCELLED}


# -- timeouts ----------------------------------------------------------------------


async def test_node_timeout_fails_node_with_timeout_error(h: Harness) -> None:
    probe = Probe()
    catalog = base_catalog(probe)
    catalog["test.hang"] = gated("test.hang", probe, asyncio.Event(), never_set())
    graph, ids = build(
        {
            "A": ("test.emit", {"value": "a"}),
            "B": ("test.hang", {}),
            "C": ("test.append", {"suffix": "c"}),
        },
        ["A.out>B.text", "B.out>C.text"],
    )
    queued = await h.queue(
        catalog,
        graph,
        timeout_policy=TimeoutPolicy(node_timeout=timedelta(seconds=0.05)),
    )
    async with asyncio.timeout(GUARD):
        await h.runtime(catalog).run(queued.id)

    loaded = await h.load(queued.id)
    error = loaded.node(ids["B"]).error
    assert loaded.status is _E.FAILED and error is not None
    assert (error.code, error.kind) == ("NODE_TIMEOUT", NodeErrorKind.TIMEOUT)
    assert loaded.node(ids["C"]).status is _N.CANCELLED
    assert probe.cleaned == [f"{ids['B']}:False"]


async def test_execution_timeout_stops_the_whole_run(h: Harness) -> None:
    # The *root* hangs, so the outcome cannot depend on how fast the
    # database is: nothing can complete before the deadline.
    probe = Probe()
    catalog = base_catalog(probe)
    catalog["test.hang"] = gated(
        "test.hang", probe, asyncio.Event(), never_set(), inputs=()
    )
    graph, ids = build(
        {"A": ("test.hang", {}), "B": ("test.append", {"suffix": "b"})},
        ["A.out>B.text"],
    )
    queued = await h.queue(
        catalog,
        graph,
        timeout_policy=TimeoutPolicy(execution_timeout=timedelta(seconds=0.1)),
    )
    async with asyncio.timeout(GUARD):
        result = await h.runtime(catalog).run(queued.id)

    loaded = await h.load(queued.id)
    assert result.status is loaded.status is _E.TIMEOUT
    assert loaded.error is not None and loaded.error.code == "EXECUTION_TIMEOUT"
    assert statuses(loaded, ids) == {"A": _N.CANCELLED, "B": _N.CANCELLED}
    assert probe.cleaned == [f"{ids['A']}:True"]


# -- cancellation --------------------------------------------------------------------


async def test_cancel_running_execution_stops_nodes_cooperatively(
    h: Harness,
) -> None:
    probe = Probe()
    catalog = base_catalog(probe)
    b_started = asyncio.Event()
    catalog["test.gate"] = gated("test.gate", probe, b_started, never_set())
    graph, ids = build(
        {
            "A": ("test.emit", {"value": "a"}),
            "B": ("test.gate", {}),
            "C": ("test.append", {"suffix": "c"}),
        },
        ["A.out>B.text", "B.out>C.text"],
    )
    queued = await h.queue(catalog, graph)
    runtime = h.runtime(catalog)
    task = asyncio.create_task(runtime.run(queued.id))
    await wait_for(b_started)

    await runtime.cancel(queued.id, reason="user request")
    await runtime.cancel(queued.id, reason="double click")  # idempotent
    async with asyncio.timeout(GUARD):
        result = await task

    loaded = await h.load(queued.id)
    assert result.status is loaded.status is _E.CANCELLED
    assert loaded.cancellation is not None
    assert loaded.cancellation.reason == "user request"
    assert statuses(loaded, ids) == {
        "A": _N.COMPLETED,
        "B": _N.CANCELLED,
        "C": _N.CANCELLED,
    }
    # The node saw the cancellation signal during its cleanup.
    assert probe.cleaned == [f"{ids['B']}:True"]
    assert ids["C"] not in probe.started


async def test_cancel_keeps_truthful_outcome_of_finished_branch(h: Harness) -> None:
    probe = Probe()
    catalog = base_catalog(probe)
    c_started = asyncio.Event()
    catalog["test.gate"] = gated("test.gate", probe, c_started, never_set())
    graph, ids = build(
        {
            "A": ("test.emit", {"value": "a"}),
            "B": ("test.append", {"suffix": "b"}),
            "C": ("test.gate", {}),
            "D": ("test.join", {}),
        },
        ["A.out>B.text", "A.out>C.text", "B.out>D.items", "C.out>D.items"],
    )
    queued = await h.queue(catalog, graph)
    runtime = h.runtime(catalog)
    task = asyncio.create_task(runtime.run(queued.id))
    await wait_for(c_started)
    await runtime.cancel(queued.id)
    async with asyncio.timeout(GUARD):
        await task

    loaded = await h.load(queued.id)
    assert loaded.status is _E.CANCELLED
    assert statuses(loaded, ids) == {
        "A": _N.COMPLETED,
        "B": _N.COMPLETED,  # really finished: recorded, not rewritten
        "C": _N.CANCELLED,
        "D": _N.CANCELLED,
    }
    assert loaded.node(ids["B"]).outputs == {"out": "ab"}


async def test_node_finishing_while_being_stopped_is_recorded_truthfully(
    h: Harness,
) -> None:
    """A node that answers cancellation by returning a result really
    completed; the record must say so rather than claim CANCELLED."""
    probe = Probe()
    catalog = base_catalog(probe)
    b_started = asyncio.Event()

    async def stubborn(ctx: NodeExecutionContext) -> Mapping[str, Any]:
        b_started.set()
        try:
            await never_set().wait()
        except asyncio.CancelledError:
            return {"out": "partial"}  # finishes instead of re-raising
        raise AssertionError("unreachable")

    catalog["test.stubborn"] = node_type("test.stubborn", stubborn, inputs=text_in())
    graph, ids = build(
        {
            "A": ("test.emit", {"value": "a"}),
            "B": ("test.stubborn", {}),
            "C": ("test.append", {"suffix": "c"}),
        },
        ["A.out>B.text", "B.out>C.text"],
    )
    queued = await h.queue(catalog, graph)
    runtime = h.runtime(catalog)
    task = asyncio.create_task(runtime.run(queued.id))
    await wait_for(b_started)
    await runtime.cancel(queued.id)
    async with asyncio.timeout(GUARD):
        await task

    loaded = await h.load(queued.id)
    assert loaded.status is _E.CANCELLED
    assert statuses(loaded, ids) == {
        "A": _N.COMPLETED,
        "B": _N.COMPLETED,
        "C": _N.CANCELLED,
    }
    assert loaded.node(ids["B"]).outputs == {"out": "partial"}


async def test_cancel_queued_execution_before_it_runs(h: Harness) -> None:
    probe = Probe()
    catalog = base_catalog(probe)
    graph, ids = build(
        {"A": ("test.emit", {"value": "a"}), "B": ("test.append", {"suffix": "b"})},
        ["A.out>B.text"],
    )
    queued = await h.queue(catalog, graph)
    runtime = h.runtime(catalog)

    await runtime.cancel(queued.id, reason="changed my mind")
    loaded = await h.load(queued.id)
    assert loaded.status is _E.CANCELLED
    assert set(statuses(loaded, ids).values()) == {_N.CANCELLED}

    with pytest.raises(InvalidStateTransitionError):
        await runtime.run(queued.id)
    assert probe.started == []


class GatedUow:
    """Real unit of work whose ``gate_call``-th use blocks until released —
    lets a test act deterministically at an exact point of a run (1st use:
    loading the execution, 2nd use: persisting its start)."""

    def __init__(self, database: Database, gate_call: int = 1) -> None:
        self.database = database
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.calls, self.gate_call = 0, gate_call

    @asynccontextmanager
    async def _gated(self) -> AsyncIterator[UnitOfWork]:
        self.calls += 1
        if self.calls == self.gate_call:
            self.entered.set()
            await self.release.wait()
        async with SqlAlchemyUnitOfWork(self.database) as uow:
            yield uow

    def __call__(self) -> Any:
        return self._gated()


async def test_cancel_while_start_is_persisted_starts_no_node(
    h: Harness, database: Database
) -> None:
    probe = Probe()
    catalog = base_catalog(probe)
    graph, ids = build(
        {"A": ("test.emit", {"value": "a"}), "B": ("test.append", {"suffix": "b"})},
        ["A.out>B.text"],
    )
    queued = await h.queue(catalog, graph)
    gate = GatedUow(database, gate_call=2)
    runtime = ExecutionRuntime(gate, catalog, clock=h.clock)
    task = asyncio.create_task(runtime.run(queued.id))
    await wait_for(gate.entered)  # RUNNING about to be persisted

    await runtime.cancel(queued.id)
    gate.release.set()
    async with asyncio.timeout(GUARD):
        result = await task

    loaded = await h.load(queued.id)
    assert result.status is loaded.status is _E.CANCELLED
    assert loaded.started_at is not None  # it did start ...
    assert probe.started == []  # ... but launched no node
    assert {loaded.node(n).attempt for n in ids.values()} == {0}


async def test_cancel_after_run_registered_but_before_start(
    h: Harness, database: Database
) -> None:
    probe = Probe()
    catalog = base_catalog(probe)
    graph, _ = build(
        {"A": ("test.emit", {"value": "a"}), "B": ("test.append", {"suffix": "b"})},
        ["A.out>B.text"],
    )
    queued = await h.queue(catalog, graph)
    gate = GatedUow(database)
    runtime = ExecutionRuntime(gate, catalog, clock=h.clock)
    task = asyncio.create_task(runtime.run(queued.id))
    await wait_for(gate.entered)

    await runtime.cancel(queued.id, reason="early")  # in-process signal
    gate.release.set()
    async with asyncio.timeout(GUARD):
        result = await task

    loaded = await h.load(queued.id)
    assert result.status is loaded.status is _E.CANCELLED
    assert loaded.started_at is None and probe.started == []


async def test_cancel_finished_execution_is_rejected(h: Harness) -> None:
    catalog = base_catalog(Probe())
    graph, _ = build(
        {"A": ("test.emit", {"value": "a"}), "B": ("test.append", {"suffix": "b"})},
        ["A.out>B.text"],
    )
    queued = await h.queue(catalog, graph)
    runtime = h.runtime(catalog)
    await runtime.run(queued.id)
    with pytest.raises(InvalidStateTransitionError):
        await runtime.cancel(queued.id)
    assert (await h.load(queued.id)).status is _E.COMPLETED


async def test_out_of_process_cancel_makes_the_owner_stop(h: Harness) -> None:
    """Another runtime instance (standing in for another process) cancels
    the persisted execution while the owner runs a node. The owner's next
    CAS save loses: it stops without overwriting CANCELLED."""
    probe = Probe()
    catalog = base_catalog(probe)
    b_started, release = asyncio.Event(), asyncio.Event()
    catalog["test.gate"] = gated("test.gate", probe, b_started, release)
    graph, ids = build(
        {
            "A": ("test.emit", {"value": "a"}),
            "B": ("test.gate", {}),
            "C": ("test.append", {"suffix": "c"}),
        },
        ["A.out>B.text", "B.out>C.text"],
    )
    queued = await h.queue(catalog, graph)
    owner = asyncio.create_task(h.runtime(catalog).run(queued.id))
    await wait_for(b_started)

    await h.runtime(catalog).cancel(queued.id, reason="elsewhere")
    release.set()
    with pytest.raises(ConcurrentModificationError):
        async with asyncio.timeout(GUARD):
            await owner

    loaded = await h.load(queued.id)
    assert loaded.status is _E.CANCELLED
    assert statuses(loaded, ids) == {
        "A": _N.COMPLETED,
        "B": _N.CANCELLED,
        "C": _N.CANCELLED,
    }
    assert ids["C"] not in probe.started


# -- duplicate execution / ownership --------------------------------------------------


async def test_same_runtime_refuses_to_run_an_execution_twice(h: Harness) -> None:
    probe = Probe()
    catalog = base_catalog(probe)
    b_started, release = asyncio.Event(), asyncio.Event()
    catalog["test.gate"] = gated("test.gate", probe, b_started, release)
    graph, ids = build(
        {"A": ("test.emit", {"value": "a"}), "B": ("test.gate", {})},
        ["A.out>B.text"],
    )
    queued = await h.queue(catalog, graph)
    runtime = h.runtime(catalog)
    first = asyncio.create_task(runtime.run(queued.id))
    await wait_for(b_started)

    with pytest.raises(ExecutionAlreadyRunningError):
        await runtime.run(queued.id)
    release.set()
    async with asyncio.timeout(GUARD):
        assert (await first).status is _E.COMPLETED
    assert probe.started == [ids["A"], ids["B"]]


async def test_two_runtimes_racing_execute_each_node_exactly_once(
    h: Harness,
) -> None:
    probe = Probe()
    catalog = base_catalog(probe)
    graph, ids = build(
        {
            "A": ("test.emit", {"value": "a"}),
            "B": ("test.append", {"suffix": "b"}),
            "C": ("test.append", {"suffix": "c"}),
        },
        ["A.out>B.text", "B.out>C.text"],
    )
    queued = await h.queue(catalog, graph)
    results = await asyncio.gather(
        h.runtime(catalog).run(queued.id),
        h.runtime(catalog).run(queued.id),
        return_exceptions=True,
    )

    winners = [r for r in results if isinstance(r, Execution)]
    losers = [r for r in results if not isinstance(r, Execution)]
    assert len(winners) == 1 and winners[0].status is _E.COMPLETED
    assert len(losers) == 1
    assert isinstance(
        losers[0], ConcurrentModificationError | InvalidStateTransitionError
    )
    assert sorted(probe.started) == sorted(ids.values())  # no duplicates
    assert (await h.load(queued.id)).status is _E.COMPLETED


async def test_completed_node_is_persisted_while_siblings_still_run(
    h: Harness,
) -> None:
    """A finished node's outputs must reach the database promptly, not only
    at the next launch or at the end of the run (recovery depends on it)."""
    probe = Probe()
    catalog = base_catalog(probe)
    c_started, release = asyncio.Event(), asyncio.Event()
    catalog["test.gate"] = gated("test.gate", probe, c_started, release)
    graph, ids = build(
        {
            "A": ("test.emit", {"value": "a"}),
            "B": ("test.append", {"suffix": "b"}),
            "C": ("test.gate", {}),
        },
        ["A.out>B.text", "A.out>C.text"],
    )
    queued = await h.queue(catalog, graph)
    task = asyncio.create_task(h.runtime(catalog).run(queued.id))
    await wait_for(c_started)

    # Bounded polling of persisted state (the only observer of a commit);
    # C stays blocked throughout, so only B's own save can satisfy it.
    for _ in range(500):
        b = (await h.load(queued.id)).node(ids["B"])
        if b.status is _N.COMPLETED:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("B's completion was not persisted while C was running")
    assert b.outputs == {"out": "ab"}
    release.set()
    async with asyncio.timeout(GUARD):
        assert (await task).status is _E.COMPLETED


async def test_outer_cancellation_stops_node_tasks_and_keeps_running_state(
    h: Harness,
) -> None:
    """A dying process (the run task cancelled) is not a clean failure: no
    node task may leak, and the persisted state must still say RUNNING
    (with RUNNING persisted *before* the node started) for recovery."""
    probe = Probe()
    catalog = base_catalog(probe)
    b_started = asyncio.Event()
    catalog["test.gate"] = gated("test.gate", probe, b_started, never_set())
    graph, ids = build(
        {"A": ("test.emit", {"value": "a"}), "B": ("test.gate", {})},
        ["A.out>B.text"],
    )
    queued = await h.queue(catalog, graph)
    task = asyncio.create_task(h.runtime(catalog).run(queued.id))
    await wait_for(b_started)

    during = await h.load(queued.id)
    assert during.status is _E.RUNNING
    assert statuses(during, ids) == {"A": _N.COMPLETED, "B": _N.RUNNING}
    assert during.node(ids["B"]).attempt == 1
    assert during.node(ids["A"]).outputs == {"out": "a"}

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert probe.cleaned == [f"{ids['B']}:True"]  # node task did not leak
    after = await h.load(queued.id)
    assert after.status is _E.RUNNING and after.revision == during.revision


# -- load scenarios -------------------------------------------------------------------


async def test_wide_fan_out_fan_in_and_deep_chain(h: Harness) -> None:
    """40 parallel branches joined in edge order, followed by a 30-node
    chain: correctness at a size where ordering bugs and N^2 surprises show."""
    catalog = base_catalog(Probe())
    width, depth = 40, 30
    nodes: dict[str, tuple[str, dict[str, Any]]] = {
        "root": ("test.emit", {"value": ""})
    }
    edges: list[str] = []
    for i in range(width):
        nodes[f"b{i}"] = ("test.append", {"suffix": f"{i}"})
        edges += [f"root.out>b{i}.text", f"b{i}.out>join.items"]
    nodes["join"] = ("test.join", {})
    previous = "join"
    for i in range(depth):
        nodes[f"c{i}"] = ("test.append", {"suffix": "."})
        edges.append(f"{previous}.out>c{i}.text")
        previous = f"c{i}"
    graph, ids = build(nodes, edges)
    queued = await h.queue(catalog, graph)

    async with asyncio.timeout(60):
        result = await h.runtime(catalog, max_concurrency=16).run(queued.id)

    assert result.status is _E.COMPLETED
    expected = "+".join(str(i) for i in range(width)) + "." * depth
    loaded = await h.load(queued.id)
    assert loaded.node(ids[previous]).outputs == {"out": expected}
    assert {ne.attempt for ne in loaded.node_executions.values()} == {1}


async def test_many_concurrent_executions_on_one_runtime_stay_isolated(
    h: Harness,
) -> None:
    catalog = base_catalog(Probe())
    runtime = h.runtime(catalog)
    queued = []
    for i in range(10):
        graph, ids = build(
            {
                "A": ("test.emit", {"value": f"run{i}"}),
                "B": ("test.append", {"suffix": "!"}),
            },
            ["A.out>B.text"],
        )
        queued.append((await h.queue(catalog, graph), ids["B"], f"run{i}!"))

    async with asyncio.timeout(60):
        results = await asyncio.gather(*(runtime.run(q.id) for q, _, _ in queued))

    assert all(r.status is _E.COMPLETED for r in results)
    for q, b, expected in queued:
        assert (await h.load(q.id)).node(b).outputs == {"out": expected}


# -- snapshot, context, persistence ------------------------------------------------


async def test_execution_runs_its_pinned_version_not_later_drafts(
    h: Harness,
) -> None:
    probe = Probe()
    catalog = base_catalog(probe)
    graph, ids = build(
        {"A": ("test.emit", {"value": "v1"}), "B": ("test.append", {"suffix": "!"})},
        ["A.out>B.text"],
    )
    project_id, version = await h.publish(catalog, graph)
    queued = await h.executions.create_execution(project_id, version)

    svc = ProjectService(h.uow, {t: c.definition for t, c in catalog.items()})
    project = await svc.get_project(project_id)
    graph2, _ = build(
        {"X": ("test.emit", {"value": "v2"}), "Y": ("test.append", {"suffix": "?"})},
        ["X.out>Y.text"],
    )
    await svc.update_draft(
        project_id,
        expected_revision=project.revision,
        name="demo",
        draft=ProjectDraft(graph=graph2),
    )
    await svc.publish_project(project_id)

    await h.runtime(catalog).run(queued.id)
    loaded = await h.load(queued.id)
    assert loaded.project_version.version == 1
    assert set(loaded.node_executions) == set(ids.values())
    assert loaded.node(ids["B"]).outputs == {"out": "v1!"}


async def test_node_context_is_scoped_and_isolated(h: Harness) -> None:
    probe = Probe()
    catalog = base_catalog(probe)

    async def meddle(ctx: NodeExecutionContext) -> Mapping[str, Any]:
        probe.enter(ctx)
        cast("dict[str, Any]", ctx.config)["suffix"] = "tampered"
        return {"out": ctx.inputs["text"]}

    catalog["test.meddle"] = node_type("test.meddle", meddle, inputs=text_in())
    graph, ids = build(
        {"A": ("test.emit", {"value": "a"}), "B": ("test.meddle", {"suffix": "x"})},
        ["A.out>B.text"],
    )
    queued = await h.queue(catalog, graph)
    result = await h.runtime(catalog).run(queued.id)

    ctx = probe.contexts[ids["B"]]
    persisted = await h.load(queued.id)
    assert ctx.execution_id == queued.id
    assert ctx.node_execution_id == persisted.node(ids["B"]).id
    assert (ctx.project_id, ctx.project_version) == (queued.project_id, "1")
    assert ctx.inputs == {"text": "a"}
    assert ctx.is_cancelled() is False
    assert ctx.runtime_metadata["attempt"] == 1
    assert ctx.runtime_metadata["project_version_id"] == queued.project_version.id
    # Node code cannot mutate the pinned snapshot through its config.
    assert result.project_version.graph.nodes[ids["B"]].config == {"suffix": "x"}


async def test_object_outputs_are_isolated_between_branches(h: Harness) -> None:
    obj = PortType.OBJECT

    async def make(ctx: NodeExecutionContext) -> Mapping[str, Any]:
        return {"out": {"items": []}}

    async def mutate(ctx: NodeExecutionContext) -> Mapping[str, Any]:
        ctx.inputs["obj"]["items"].append("mutated")
        return {"out": ctx.inputs["obj"]}

    catalog: dict[str, type[Node]] = {
        "test.make": node_type("test.make", make, outputs=(NodeOutput("out", obj),)),
        "test.mutate": node_type(
            "test.mutate",
            mutate,
            inputs=(NodeInput("obj", obj),),
            outputs=(NodeOutput("out", obj),),
        ),
    }
    graph, ids = build(
        {"A": ("test.make", {}), "B": ("test.mutate", {}), "C": ("test.mutate", {})},
        ["A.out>B.obj", "A.out>C.obj"],
    )
    queued = await h.queue(catalog, graph)
    await h.runtime(catalog).run(queued.id)

    loaded = await h.load(queued.id)
    assert loaded.node(ids["A"]).outputs == {"out": {"items": []}}
    assert loaded.node(ids["B"]).outputs == {"out": {"items": ["mutated"]}}
    assert loaded.node(ids["C"]).outputs == {"out": {"items": ["mutated"]}}


async def test_full_state_round_trips_through_the_database(h: Harness) -> None:
    catalog = base_catalog(Probe())
    graph, ids = build(
        {"A": ("test.emit", {"value": "a"}), "B": ("test.fail", {})},
        ["A.out>B.text"],
    )
    policy = TimeoutPolicy(
        execution_timeout=timedelta(minutes=5), node_timeout=timedelta(seconds=30)
    )
    queued = await h.queue(catalog, graph, timeout_policy=policy)
    assert (await h.load(queued.id)).status is _E.QUEUED
    result = await h.runtime(catalog).run(queued.id)

    loaded = await h.load(queued.id)
    assert loaded.revision == result.revision
    assert loaded.metadata == result.metadata
    assert loaded.metadata.correlation_id == "corr-1"
    assert loaded.timeout_policy == policy
    assert loaded.retry_policy == RetryPolicy()
    assert loaded.error == result.error
    assert (loaded.created_at, loaded.started_at, loaded.finished_at) == (
        result.created_at,
        result.started_at,
        result.finished_at,
    )
    for nid in ids.values():
        mine, theirs = loaded.node(nid), result.node(nid)
        assert (mine.id, mine.status, mine.attempt, mine.failures, mine.outputs) == (
            theirs.id,
            theirs.status,
            theirs.attempt,
            theirs.failures,
            theirs.outputs,
        )
        assert (mine.started_at, mine.finished_at) == (
            theirs.started_at,
            theirs.finished_at,
        )


# -- guards --------------------------------------------------------------------------


async def test_run_unknown_execution(h: Harness) -> None:
    with pytest.raises(ExecutionNotFoundError):
        await h.runtime({}).run("exec_" + "0" * 32)


async def test_create_execution_for_missing_version(h: Harness) -> None:
    catalog = base_catalog(Probe())
    graph, _ = build(
        {"A": ("test.emit", {"value": "a"}), "B": ("test.append", {"suffix": "b"})},
        ["A.out>B.text"],
    )
    project_id, _ = await h.publish(catalog, graph)
    with pytest.raises(ProjectVersionNotFoundError):
        await h.executions.create_execution(project_id, 99)


async def test_configured_retry_policy_is_refused_not_ignored(h: Harness) -> None:
    catalog = base_catalog(Probe())
    graph, _ = build(
        {"A": ("test.emit", {"value": "a"}), "B": ("test.append", {"suffix": "b"})},
        ["A.out>B.text"],
    )
    project_id, version = await h.publish(catalog, graph)
    async with h.uow() as uow:
        snapshot = await uow.versions.get(project_id, version)
        assert snapshot is not None
        execution = Execution.create(
            project_id, snapshot, retry_policy=RetryPolicy(max_attempts=3)
        )
        execution.enqueue()
        await uow.executions.save(execution)
        await uow.commit()

    with pytest.raises(ConfigurationError):
        await h.runtime(catalog).run(execution.id)
    assert (await h.load(execution.id)).status is _E.QUEUED
