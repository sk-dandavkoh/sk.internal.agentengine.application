"""Reliability + recovery against real PostgreSQL (roadmap Phase 23).

Every scenario injects a failure — a failing or hanging node, a killed run
task (the process dying), a lost owner, a duplicate request, a torn state —
then lets the runtime recover and asserts on the *persisted* state and on
what node code actually did (``Probe``). Synchronization is event-driven;
the only wall-clock waits are the configured backoffs/limits under test, all
far below ``GUARD``.

A crash is simulated the way it happens: the run task is cancelled, which
stops node tasks and leaves the execution RUNNING in the database with its
heartbeat frozen. "Time passing" (the heartbeat going stale) is the row's
heartbeat moved back in SQL — what the clock does, without waiting for it.
"""

import asyncio
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from akasha.application.persistence import ConcurrentModificationError
from akasha.domain.event import Event, EventType
from akasha.domain.execution import (
    Execution,
    ExecutionMetadata,
    ExecutionStatus,
    NodeExecutionStatus,
    RetryPolicy,
    TimeoutPolicy,
)
from akasha.domain.graph import Graph
from akasha.domain.node import (
    Node,
    NodeCapabilities,
    NodeError,
    NodeErrorKind,
    NodeExecutionContext,
)
from akasha.errors import InvalidStateTransitionError
from akasha.infrastructure.database import Database
from akasha.observability.metrics import EngineMetrics
from akasha.runtime.plan import NodeCatalog
from akasha.runtime.runtime import ExecutionOwnershipLostError, ExecutionRuntime
from tests.integration.test_runtime import (
    GUARD,
    Harness,
    Probe,
    base_catalog,
    build,
    gated,
    never_set,
    node_type,
    statuses,
    text_in,
    wait_for,
)

pytestmark = pytest.mark.usefixtures("clean_db")

_E = ExecutionStatus
_N = NodeExecutionStatus
STALE = timedelta(minutes=1)
RETRY3 = RetryPolicy(max_attempts=3, initial_backoff=timedelta(0))


@pytest.fixture
def h(database: Database) -> Harness:
    return Harness(database)


# -- helpers -----------------------------------------------------------------------


async def queue(
    h: Harness,
    catalog: NodeCatalog,
    graph: Graph,
    *,
    retry_policy: RetryPolicy | None = None,
    timeout_policy: TimeoutPolicy | None = None,
) -> Execution:
    project_id, version = await h.publish(catalog, graph)
    return await h.executions.create_execution(
        project_id,
        version,
        metadata=ExecutionMetadata(correlation_id="corr-1"),
        retry_policy=retry_policy,
        timeout_policy=timeout_policy,
    )


def flaky(
    type_id: str,
    probe: Probe,
    failures: list[NodeErrorKind],
    seen: list[tuple[str, object]] | None = None,
) -> type[Node]:
    """Fails with the next kind of ``failures`` per attempt, then succeeds.
    ``seen`` collects each attempt's (node_execution_id, attempt)."""

    async def run(ctx: NodeExecutionContext) -> Mapping[str, Any]:
        probe.enter(ctx)
        if seen is not None:
            seen.append((ctx.node_execution_id, ctx.runtime_metadata["attempt"]))
        if failures:
            raise NodeError("flaky dependency", kind=failures.pop(0))
        return {"out": ctx.inputs["text"] + "+ok"}

    return node_type(type_id, run, inputs=text_in())


def with_side_effects(node: type[Node]) -> type[Node]:
    changed = replace(
        node.definition, capabilities=NodeCapabilities(has_side_effects=True)
    )
    return type(node.__name__, (node,), {"definition": changed})


async def events_of(h: Harness, execution_id: str) -> list[Event]:
    async with h.uow() as uow:
        return list(await uow.events.list(execution_id=execution_id, limit=1000))


def types_of(events: list[Event]) -> list[EventType]:
    return [e.event_type for e in events]


async def make_stale(h: Harness, execution_id: str) -> None:
    async with h.database.engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE executions SET heartbeat_at = now() - interval '1 hour'"
                " WHERE id = :id"
            ),
            {"id": execution_id},
        )


async def crash(task: asyncio.Task[Execution]) -> None:
    """The owning process dies: its run task is cancelled mid-flight."""
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def eventually(h: Harness, execution_id: str, check: Any) -> Execution:
    """Bounded poll of persisted state (never a fixed sleep)."""
    async with asyncio.timeout(GUARD):
        while True:
            loaded = await h.load(execution_id)
            if check(loaded):
                return loaded
            await asyncio.sleep(0.01)


def a_b_c(catalog: dict[str, type[Node]], b_type: str) -> tuple[Graph, dict[str, str]]:
    return build(
        {
            "A": ("test.emit", {"value": "a"}),
            "B": (b_type, {}),
            "C": ("test.append", {"suffix": "c"}),
        },
        ["A.out>B.text", "B.out>C.text"],
    )


# -- retry / backoff ---------------------------------------------------------------


async def test_transient_failure_is_retried_then_succeeds(h: Harness) -> None:
    probe = Probe()
    catalog = base_catalog(probe)
    seen: list[tuple[str, object]] = []
    catalog["test.flaky"] = flaky("test.flaky", probe, [NodeErrorKind.TRANSIENT], seen)
    graph, ids = a_b_c(catalog, "test.flaky")
    metrics = EngineMetrics()
    queued = await queue(h, catalog, graph, retry_policy=RETRY3)

    result = await h.runtime(catalog, metrics=metrics).run(queued.id)

    assert result.status is _E.COMPLETED
    assert probe.started.count(ids["B"]) == 2
    assert probe.started.count(ids["A"]) == 1  # upstream never re-run
    loaded = await h.load(queued.id)
    b = loaded.node(ids["B"])
    assert (b.status, b.attempt) == (_N.COMPLETED, 2)
    assert [f.kind for f in b.failures] == [NodeErrorKind.TRANSIENT]  # history kept
    # The idempotency key a side-effecting node sends is stable across
    # attempts; the attempt number tells the attempts apart.
    assert seen == [(b.id, 1), (b.id, 2)]
    assert loaded.node(ids["C"]).outputs == {"out": "a+okc"}
    b_events = [e for e in await events_of(h, queued.id) if e.node_id == ids["B"]]
    assert types_of(b_events) == [
        EventType.NODE_STARTED,
        EventType.NODE_RETRY_SCHEDULED,
        EventType.NODE_STARTED,
        EventType.NODE_COMPLETED,
    ]
    assert b_events[1].payload["attempt"] == 1
    assert b_events[2].payload["attempt"] == 2
    assert (
        metrics.registry.get_sample_value(
            "akasha_node_retries_total",
            {"node_type": "test.flaky", "error_kind": "transient"},
        )
        == 1
    )


async def test_retries_exhausted_fail_the_execution(h: Harness) -> None:
    probe = Probe()
    catalog = base_catalog(probe)
    catalog["test.flaky"] = flaky("test.flaky", probe, [NodeErrorKind.TRANSIENT] * 5)
    graph, ids = a_b_c(catalog, "test.flaky")
    queued = await queue(h, catalog, graph, retry_policy=RETRY3)

    result = await h.runtime(catalog).run(queued.id)

    assert result.status is _E.FAILED
    assert probe.started.count(ids["B"]) == 3  # max_attempts, then stop
    loaded = await h.load(queued.id)
    assert loaded.node(ids["B"]).attempt == 3
    assert len(loaded.node(ids["B"]).failures) == 3
    assert statuses(loaded, ids)["C"] is _N.CANCELLED


async def test_permanent_failure_is_never_retried(h: Harness) -> None:
    probe = Probe()
    catalog = base_catalog(probe)
    catalog["test.flaky"] = flaky("test.flaky", probe, [NodeErrorKind.PERMANENT])
    graph, ids = a_b_c(catalog, "test.flaky")
    queued = await queue(h, catalog, graph, retry_policy=RETRY3)

    result = await h.runtime(catalog).run(queued.id)

    assert result.status is _E.FAILED
    assert probe.started.count(ids["B"]) == 1
    assert result.error is not None and result.error.kind is NodeErrorKind.PERMANENT


async def test_default_policy_does_not_retry(h: Harness) -> None:
    probe = Probe()
    catalog = base_catalog(probe)
    catalog["test.flaky"] = flaky("test.flaky", probe, [NodeErrorKind.TRANSIENT])
    graph, ids = a_b_c(catalog, "test.flaky")
    queued = await queue(h, catalog, graph)

    assert (await h.runtime(catalog).run(queued.id)).status is _E.FAILED
    assert probe.started.count(ids["B"]) == 1


async def test_backoff_is_exponential_and_recorded(h: Harness) -> None:
    probe = Probe()
    catalog = base_catalog(probe)
    catalog["test.flaky"] = flaky("test.flaky", probe, [NodeErrorKind.TRANSIENT] * 2)
    graph, _ = a_b_c(catalog, "test.flaky")
    policy = RetryPolicy(
        max_attempts=3,
        initial_backoff=timedelta(milliseconds=20),
        backoff_multiplier=2,
    )
    queued = await queue(h, catalog, graph, retry_policy=policy)

    # Real clock: the backoff is honoured in wall time, not only recorded.
    started = datetime.now(UTC)
    result = await ExecutionRuntime(h.uow, catalog).run(queued.id)

    assert result.status is _E.COMPLETED
    assert datetime.now(UTC) - started >= timedelta(milliseconds=60)
    retries = [
        e.payload["backoff_ms"]
        for e in await events_of(h, queued.id)
        if e.event_type is EventType.NODE_RETRY_SCHEDULED
    ]
    assert retries == [20, 40]


async def test_node_timeout_is_retried(h: Harness) -> None:
    probe = Probe()
    catalog = base_catalog(probe)
    attempts: list[int] = []

    async def slow_once(ctx: NodeExecutionContext) -> Mapping[str, Any]:
        probe.enter(ctx)
        attempts.append(1)
        if len(attempts) == 1:
            await never_set().wait()  # the first attempt hangs
        return {"out": "second try"}

    catalog["test.slow"] = node_type("test.slow", slow_once, inputs=text_in())
    graph, ids = a_b_c(catalog, "test.slow")
    queued = await queue(
        h,
        catalog,
        graph,
        retry_policy=RETRY3,
        timeout_policy=TimeoutPolicy(node_timeout=timedelta(milliseconds=50)),
    )

    result = await h.runtime(catalog).run(queued.id)

    assert result.status is _E.COMPLETED
    b = (await h.load(queued.id)).node(ids["B"])
    assert [f.code for f in b.failures] == ["NODE_TIMEOUT"]
    assert b.attempt == 2


async def test_retry_state_is_persisted_before_the_backoff_wait(h: Harness) -> None:
    """A crash during a backoff must find the retry decision on disk."""
    probe = Probe()
    catalog = base_catalog(probe)
    catalog["test.flaky"] = flaky("test.flaky", probe, [NodeErrorKind.TRANSIENT])
    graph, ids = a_b_c(catalog, "test.flaky")
    policy = RetryPolicy(max_attempts=3, initial_backoff=timedelta(minutes=5))
    queued = await queue(h, catalog, graph, retry_policy=policy)
    task = asyncio.create_task(ExecutionRuntime(h.uow, catalog).run(queued.id))

    waiting = await eventually(
        h,
        queued.id,
        lambda e: e.node(ids["B"]).status is _N.READY and e.node(ids["B"]).failures,
    )
    assert waiting.node(ids["B"]).retry_at is not None
    assert waiting.status is _E.RUNNING
    await crash(task)
    assert probe.started.count(ids["B"]) == 1


# -- timeout / cancellation during retry -------------------------------------------


async def test_cancel_during_retry_backoff_is_immediate_and_never_revived(
    h: Harness,
) -> None:
    probe = Probe()
    catalog = base_catalog(probe)
    catalog["test.flaky"] = flaky("test.flaky", probe, [NodeErrorKind.TRANSIENT])
    graph, ids = a_b_c(catalog, "test.flaky")
    policy = RetryPolicy(max_attempts=3, initial_backoff=timedelta(minutes=5))
    queued = await queue(h, catalog, graph, retry_policy=policy)
    runtime = ExecutionRuntime(h.uow, catalog)
    task = asyncio.create_task(runtime.run(queued.id))
    await eventually(h, queued.id, lambda e: e.node(ids["B"]).status is _N.READY)

    await runtime.cancel(queued.id, reason="stop")
    async with asyncio.timeout(GUARD):  # far below the 5 min backoff
        result = await task

    assert result.status is _E.CANCELLED
    loaded = await h.load(queued.id)
    assert statuses(loaded, ids) == {
        "A": _N.COMPLETED,
        "B": _N.CANCELLED,
        "C": _N.CANCELLED,
    }
    assert probe.started.count(ids["B"]) == 1


async def test_execution_timeout_during_retry_backoff(h: Harness) -> None:
    probe = Probe()
    catalog = base_catalog(probe)
    catalog["test.flaky"] = flaky("test.flaky", probe, [NodeErrorKind.TRANSIENT])
    graph, ids = a_b_c(catalog, "test.flaky")
    queued = await queue(
        h,
        catalog,
        graph,
        retry_policy=RetryPolicy(max_attempts=3, initial_backoff=timedelta(minutes=5)),
        timeout_policy=TimeoutPolicy(execution_timeout=timedelta(milliseconds=300)),
    )

    async with asyncio.timeout(GUARD):
        result = await ExecutionRuntime(h.uow, catalog).run(queued.id)

    assert result.status is _E.TIMEOUT
    assert result.error is not None and result.error.code == "EXECUTION_TIMEOUT"
    assert probe.started.count(ids["B"]) == 1


async def test_cancellation_error_from_a_node_is_final(h: Harness) -> None:
    probe = Probe()
    catalog = base_catalog(probe)
    catalog["test.flaky"] = flaky("test.flaky", probe, [NodeErrorKind.CANCELLATION])
    graph, ids = a_b_c(catalog, "test.flaky")
    queued = await queue(h, catalog, graph, retry_policy=RETRY3)

    result = await h.runtime(catalog).run(queued.id)

    assert result.status is _E.FAILED
    assert probe.started.count(ids["B"]) == 1


# -- worker crash / process restart ------------------------------------------------


async def crashed_mid_b(
    h: Harness, *, side_effects: bool = False, **policies: Any
) -> tuple[Execution, dict[str, str], Probe, dict[str, type[Node]]]:
    """A completed, B in flight when the owning process dies."""
    probe = Probe()
    catalog = base_catalog(probe)
    b_started = asyncio.Event()
    gate = gated("test.gate", probe, b_started, never_set())
    catalog["test.gate"] = with_side_effects(gate) if side_effects else gate
    graph, ids = a_b_c(catalog, "test.gate")
    queued = await queue(h, catalog, graph, **policies)
    task = asyncio.create_task(h.runtime(catalog).run(queued.id))
    await wait_for(b_started)
    await crash(task)
    assert (await h.load(queued.id)).status is _E.RUNNING  # never silently lost
    return queued, ids, probe, catalog


def released(catalog: dict[str, type[Node]], probe: Probe) -> dict[str, type[Node]]:
    """The restarted process: same node types, B now finishes."""
    done = asyncio.Event()
    done.set()
    return {**catalog, "test.gate": gated("test.gate", probe, asyncio.Event(), done)}


async def test_process_restart_resumes_without_rerunning_finished_nodes(
    h: Harness,
) -> None:
    queued, ids, probe, catalog = await crashed_mid_b(h)
    await make_stale(h, queued.id)
    first_start = (await h.load(queued.id)).started_at

    restarted = h.runtime(released(catalog, probe))
    [recovered] = await restarted.recover_stale(stale_after=STALE)
    assert recovered.status is _E.QUEUED
    persisted = await h.load(queued.id)
    assert statuses(persisted, ids) == {
        "A": _N.COMPLETED,
        "B": _N.READY,
        "C": _N.PENDING,
    }
    assert [f.code for f in persisted.node(ids["B"]).failures] == ["NODE_INTERRUPTED"]

    result = await restarted.run(queued.id)

    assert result.status is _E.COMPLETED
    assert probe.started.count(ids["A"]) == 1  # finished work is never redone
    assert probe.started.count(ids["B"]) == 2  # interrupted, side-effect free
    assert probe.started.count(ids["C"]) == 1
    assert result.node(ids["C"]).outputs == {"out": "a!c"}
    assert result.started_at == first_start  # resumed, not restarted
    kinds = types_of(await events_of(h, queued.id))
    assert kinds.count(EventType.EXECUTION_STARTED) == 1
    recovered_at = kinds.index(EventType.EXECUTION_RECOVERED)
    assert kinds[recovered_at + 1] is EventType.EXECUTION_RESUMED
    assert kinds[-1] is EventType.EXECUTION_COMPLETED


async def test_interrupted_side_effect_is_never_silently_repeated(h: Harness) -> None:
    queued, ids, probe, catalog = await crashed_mid_b(h, side_effects=True)
    await make_stale(h, queued.id)

    [recovered] = await h.runtime(catalog).recover_stale(stale_after=STALE)

    assert recovered.status is _E.FAILED
    loaded = await h.load(queued.id)
    assert loaded.error is not None
    assert (loaded.error.code, loaded.error.node_id) == ("NODE_INTERRUPTED", ids["B"])
    assert statuses(loaded, ids) == {
        "A": _N.COMPLETED,
        "B": _N.FAILED,
        "C": _N.CANCELLED,
    }
    assert probe.started.count(ids["B"]) == 1


async def test_retry_policy_opts_an_interrupted_side_effect_into_repeat(
    h: Harness,
) -> None:
    queued, ids, probe, catalog = await crashed_mid_b(
        h, side_effects=True, retry_policy=RETRY3
    )
    await make_stale(h, queued.id)
    runtime = h.runtime(released(catalog, probe))
    [recovered] = await runtime.recover_stale(stale_after=STALE)
    assert recovered.status is _E.QUEUED

    assert (await runtime.run(queued.id)).status is _E.COMPLETED
    assert probe.started.count(ids["B"]) == 2


async def test_live_owner_is_never_recovered(h: Harness) -> None:
    probe = Probe()
    catalog = base_catalog(probe)
    b_started, release = asyncio.Event(), asyncio.Event()
    catalog["test.gate"] = gated("test.gate", probe, b_started, release)
    graph, _ = a_b_c(catalog, "test.gate")
    queued = await queue(h, catalog, graph)
    owner = h.runtime(catalog)
    task = asyncio.create_task(owner.run(queued.id))
    await wait_for(b_started)
    before = await h.load(queued.id)

    # Fresh heartbeat: another process leaves it alone.
    assert await h.runtime(catalog).recover_stale(stale_after=STALE) == []
    # The owner's own recovery pass skips it even when late.
    await make_stale(h, queued.id)
    assert await owner.recover_stale(stale_after=STALE) == []
    assert (await h.load(queued.id)).revision == before.revision

    release.set()
    assert (await task).status is _E.COMPLETED


async def test_owner_that_lost_its_run_stops_within_a_heartbeat(h: Harness) -> None:
    """A hung-but-alive owner is declared stale and taken over; its next
    heartbeat notices and stops its node instead of running on unseen."""
    probe = Probe()
    catalog = base_catalog(probe)
    b_started = asyncio.Event()
    catalog["test.gate"] = gated("test.gate", probe, b_started, never_set())
    graph, ids = a_b_c(catalog, "test.gate")
    queued = await queue(h, catalog, graph)
    owner = h.runtime(catalog, heartbeat_seconds=0.05)
    task = asyncio.create_task(owner.run(queued.id))
    await wait_for(b_started)

    # The owner keeps heart-beating; a zero threshold declares it stale
    # anyway (a hang past the threshold). Polled: a beat may land between
    # the scan and the claim, and then the claim rightly backs off.
    recoverer = h.runtime(catalog)
    async with asyncio.timeout(GUARD):
        while True:
            recovered = await recoverer.recover_stale(stale_after=timedelta(0))
            if recovered:
                break
            await asyncio.sleep(0.01)
    assert recovered[0].status is _E.QUEUED

    with pytest.raises(ExecutionOwnershipLostError):
        async with asyncio.timeout(GUARD):
            await task
    assert probe.cleaned == [f"{ids['B']}:True"]  # node stopped, cooperatively
    assert (await h.load(queued.id)).status is _E.QUEUED  # takeover intact


async def test_out_of_process_cancel_reaches_an_idle_owner_via_heartbeat(
    h: Harness,
) -> None:
    """Cancellation must not stay a database flag the runtime never sees
    (backend AGENTS §41): the owner's heartbeat surfaces it even while its
    node would block forever."""
    probe = Probe()
    catalog = base_catalog(probe)
    b_started = asyncio.Event()
    catalog["test.gate"] = gated("test.gate", probe, b_started, never_set())
    graph, ids = a_b_c(catalog, "test.gate")
    queued = await queue(h, catalog, graph)
    task = asyncio.create_task(
        h.runtime(catalog, heartbeat_seconds=0.05).run(queued.id)
    )
    await wait_for(b_started)

    await h.runtime(catalog).cancel(queued.id, reason="elsewhere")

    with pytest.raises(ConcurrentModificationError):
        async with asyncio.timeout(GUARD):
            await task
    assert probe.cleaned == [f"{ids['B']}:True"]
    assert (await h.load(queued.id)).status is _E.CANCELLED


async def test_stale_execution_with_cancel_request_ends_cancelled(h: Harness) -> None:
    queued, ids, probe, catalog = await crashed_mid_b(h, retry_policy=RETRY3)
    execution = await h.load(queued.id)
    execution.request_cancellation(reason="user", now=datetime.now(UTC))
    async with h.uow() as uow:
        await uow.executions.save(execution)
        await uow.commit()
    await make_stale(h, queued.id)

    [recovered] = await h.runtime(catalog).recover_stale(stale_after=STALE)

    assert recovered.status is _E.CANCELLED
    assert probe.started.count(ids["B"]) == 1


async def test_stale_execution_past_its_deadline_times_out(h: Harness) -> None:
    queued, _, _, catalog = await crashed_mid_b(
        h, timeout_policy=TimeoutPolicy(execution_timeout=timedelta(seconds=30))
    )
    await make_stale(h, queued.id)
    later = h.clock.now + timedelta(hours=1)

    [recovered] = await ExecutionRuntime(
        h.uow, catalog, clock=lambda: later
    ).recover_stale(stale_after=STALE)

    assert recovered.status is _E.TIMEOUT


async def test_resumed_run_keeps_the_original_deadline(h: Harness) -> None:
    """The deadline counts from the first start: a resume 29.9 s into a
    30 s budget times out almost at once, not 30 s later."""
    queued, _, _, catalog = await crashed_mid_b(
        h, timeout_policy=TimeoutPolicy(execution_timeout=timedelta(seconds=30))
    )
    await make_stale(h, queued.id)
    started = (await h.load(queued.id)).started_at
    assert started is not None
    almost = started + timedelta(seconds=29.9)
    runtime = ExecutionRuntime(h.uow, catalog, clock=lambda: almost)
    [recovered] = await runtime.recover_stale(stale_after=STALE)
    assert recovered.status is _E.QUEUED

    async with asyncio.timeout(GUARD):  # a fresh 30 s budget would trip this
        result = await runtime.run(queued.id)
    assert result.status is _E.TIMEOUT


async def test_resume_refuses_changed_node_versions(h: Harness) -> None:
    queued, ids, probe, catalog = await crashed_mid_b(h)
    await make_stale(h, queued.id)
    runtime = h.runtime(catalog)
    [_] = await runtime.recover_stale(stale_after=STALE)
    async with h.database.engine.begin() as conn:
        # As if the node type was upgraded between the crash and the resume.
        await conn.execute(
            text(
                "UPDATE executions SET metadata = jsonb_set(metadata,"
                " '{node_versions,test.gate}', '\"0.9.0\"') WHERE id = :id"
            ),
            {"id": queued.id},
        )

    result = await runtime.run(queued.id)

    assert result.status is _E.FAILED
    assert result.error is not None and result.error.code == "NODE_VERSIONS_CHANGED"
    assert probe.started.count(ids["B"]) == 1


# -- duplicates --------------------------------------------------------------------


async def test_concurrent_recoverers_take_over_exactly_once(h: Harness) -> None:
    queued, ids, _, catalog = await crashed_mid_b(h)
    await make_stale(h, queued.id)

    results = await asyncio.gather(
        *(h.runtime(catalog).recover_stale(stale_after=STALE) for _ in range(4))
    )

    assert sorted(len(r) for r in results) == [0, 0, 0, 1]
    loaded = await h.load(queued.id)
    assert len(loaded.node(ids["B"]).failures) == 1  # settled once
    kinds = types_of(await events_of(h, queued.id))
    assert kinds.count(EventType.EXECUTION_RECOVERED) == 1


async def test_duplicate_resume_requests_run_it_once(h: Harness) -> None:
    queued, ids, probe, catalog = await crashed_mid_b(h)
    await make_stale(h, queued.id)
    catalog = released(catalog, probe)
    await h.runtime(catalog).recover_stale(stale_after=STALE)

    outcomes = await asyncio.gather(
        h.runtime(catalog).run(queued.id),
        h.runtime(catalog).run(queued.id),
        return_exceptions=True,
    )

    losers = [o for o in outcomes if isinstance(o, BaseException)]
    assert len(losers) == 1
    assert isinstance(
        losers[0], ConcurrentModificationError | InvalidStateTransitionError
    )
    assert (await h.load(queued.id)).status is _E.COMPLETED
    assert probe.started.count(ids["B"]) == 2  # crashed attempt + one resume
    assert probe.started.count(ids["C"]) == 1


async def test_recovering_a_finished_execution_is_a_no_op(h: Harness) -> None:
    probe = Probe()
    catalog = base_catalog(probe)
    graph, _ = build(
        {"A": ("test.emit", {"value": "a"}), "C": ("test.append", {"suffix": "c"})},
        ["A.out>C.text"],
    )
    queued = await queue(h, catalog, graph)
    await h.runtime(catalog).run(queued.id)
    await make_stale(h, queued.id)

    assert await h.runtime(catalog).recover_stale(stale_after=STALE) == []
    assert (await h.load(queued.id)).status is _E.COMPLETED


# -- partially persisted state -----------------------------------------------------


async def test_torn_progress_is_rederived_on_resume(h: Harness) -> None:
    """A completed node whose downstream was never marked READY (state torn
    between two writes) must not strand the run: resume re-derives
    readiness from what finished."""
    probe = Probe()
    catalog = base_catalog(probe)
    graph, ids = build(
        {"A": ("test.emit", {"value": "a"}), "C": ("test.append", {"suffix": "c"})},
        ["A.out>C.text"],
    )
    queued = await queue(h, catalog, graph)
    execution = await h.load(queued.id)
    execution.start(
        now=h.clock(), node_versions={"test.emit": "1.0.0", "test.append": "1.0.0"}
    )
    execution.mark_node_ready(ids["A"])
    execution.start_node(ids["A"], now=h.clock())
    execution.complete_node(ids["A"], outputs={"out": "a"}, now=h.clock())
    async with h.uow() as uow:  # C left PENDING: the torn part
        await uow.executions.save(execution)
        await uow.commit()
    await make_stale(h, queued.id)

    runtime = h.runtime(catalog)
    [recovered] = await runtime.recover_stale(stale_after=STALE)
    assert recovered.node(ids["C"]).status is _N.PENDING

    result = await runtime.run(queued.id)
    assert result.status is _E.COMPLETED
    assert result.node(ids["C"]).outputs == {"out": "ac"}
    assert ids["A"] not in probe.started  # its output was already persisted


async def test_crash_right_after_running_was_persisted(h: Harness) -> None:
    """RUNNING is saved before node code runs, so a crash in between looks
    exactly like an interrupted attempt — and is settled the same way."""
    probe = Probe()
    catalog = base_catalog(probe)
    graph, ids = build(
        {"A": ("test.emit", {"value": "a"}), "C": ("test.append", {"suffix": "c"})},
        ["A.out>C.text"],
    )
    queued = await queue(h, catalog, graph)
    execution = await h.load(queued.id)
    execution.start(
        now=h.clock(), node_versions={"test.emit": "1.0.0", "test.append": "1.0.0"}
    )
    execution.mark_node_ready(ids["A"])
    execution.start_node(ids["A"], now=h.clock())
    async with h.uow() as uow:
        await uow.executions.save(execution)
        await uow.commit()
    await make_stale(h, queued.id)

    runtime = h.runtime(catalog)
    await runtime.recover_stale(stale_after=STALE)
    result = await runtime.run(queued.id)

    assert result.status is _E.COMPLETED
    assert result.node(ids["A"]).attempt == 2
    assert probe.started == [ids["A"], ids["C"]]  # A's code ran once in all
