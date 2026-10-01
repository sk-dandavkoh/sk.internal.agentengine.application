"""Resource limits against real PostgreSQL (roadmap Phase 24).

Each cap is driven exactly to its boundary and one step beyond. Assertions
are made on the *persisted* state (what an operator or the API sees), on
what node code actually did (``Probe``) and on the limit metric. The only
wall-clock waits are the sub-second timeouts under test, all far below
``GUARD``.
"""

import asyncio
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import func, select

from akasha.application.executions import ExecutionService
from akasha.domain.execution import (
    Execution,
    ExecutionStatus,
    NodeExecutionStatus,
    RetryPolicy,
    TimeoutPolicy,
)
from akasha.domain.graph import Graph
from akasha.domain.limits import (
    ExecutionCapacityError,
    Limit,
    ResourceLimitError,
    ResourceLimits,
    json_size,
)
from akasha.domain.node import NodeExecutionContext
from akasha.errors import InvalidStateTransitionError
from akasha.infrastructure import db_models as m
from akasha.infrastructure.database import Database
from akasha.observability.metrics import EngineMetrics
from akasha.runtime.plan import NodeCatalog
from tests.integration.test_reliability import crash, eventually, make_stale
from tests.integration.test_runtime import (
    GUARD,
    Harness,
    Probe,
    base_catalog,
    build,
    gated,
    never_set,
    node_type,
    text_in,
    wait_for,
)

pytestmark = pytest.mark.usefixtures("clean_db")

_E = ExecutionStatus
_N = NodeExecutionStatus
STALE = timedelta(minutes=1)

# {"out": "<k x's>"} serializes to 10 + k bytes.
ENVELOPE = json_size({"out": ""})


def x(size: int) -> str:
    """A string whose ``{"out": ...}`` output is exactly ``size`` bytes."""
    return "x" * (size - ENVELOPE)


def limits(**kwargs: Any) -> ResourceLimits:
    return ResourceLimits(**kwargs)


@pytest.fixture
def h(database: Database) -> Harness:
    return Harness(database)


def service(
    h: Harness, lim: ResourceLimits, metrics: EngineMetrics | None = None
) -> ExecutionService:
    return ExecutionService(h.uow, metrics=metrics, limits=lim)


def rejected(metrics: EngineMetrics, limit: Limit) -> float:
    value = metrics.registry.get_sample_value(
        "akasha_resource_limit_exceeded_total", {"limit": limit.value}
    )
    return value or 0.0


async def stored_executions(h: Harness) -> int:
    async with h.database.engine.connect() as conn:
        count = await conn.scalar(select(func.count()).select_from(m.executions))
    return int(count or 0)


def chain(n: int) -> Graph:
    nodes = {"N0": ("test.emit", {"value": "v"})}
    nodes |= {f"N{i}": ("test.append", {"suffix": "."}) for i in range(1, n)}
    edges = [f"N{i - 1}.out>N{i}.text" for i in range(1, n)]
    return build(nodes, edges)[0]


async def run(
    h: Harness,
    catalog: NodeCatalog,
    graph: Graph,
    lim: ResourceLimits,
    *,
    retry_policy: RetryPolicy | None = None,
    metrics: EngineMetrics | None = None,
) -> Execution:
    project_id, version = await h.publish(catalog, graph)
    queued = await service(h, lim).create_execution(
        project_id, version, retry_policy=retry_policy
    )
    async with asyncio.timeout(GUARD):
        await h.runtime(catalog, limits=lim, metrics=metrics).run(queued.id)
    return await h.load(queued.id)


# -- admission: durations and attempts ------------------------------------------


async def test_unset_policies_are_persisted_as_the_caps(h: Harness) -> None:
    lim = limits(
        execution_timeout=timedelta(seconds=90), node_timeout=timedelta(seconds=9)
    )
    catalog = base_catalog(Probe())
    project_id, version = await h.publish(catalog, chain(2))
    created = await service(h, lim).create_execution(project_id, version)

    persisted = await h.load(created.id)
    assert persisted.timeout_policy == TimeoutPolicy(
        timedelta(seconds=90), timedelta(seconds=9)
    )
    assert persisted.retry_policy == RetryPolicy()


@pytest.mark.parametrize(
    ("policies", "limit"),
    [
        (
            {"timeout_policy": TimeoutPolicy(execution_timeout=timedelta(seconds=61))},
            Limit.EXECUTION_DURATION,
        ),
        (
            {"timeout_policy": TimeoutPolicy(node_timeout=timedelta(seconds=11))},
            Limit.NODE_DURATION,
        ),
        ({"retry_policy": RetryPolicy(max_attempts=4)}, Limit.NODE_ATTEMPTS),
    ],
)
async def test_policy_beyond_a_cap_is_rejected_and_nothing_is_stored(
    h: Harness, policies: dict[str, Any], limit: Limit
) -> None:
    lim = limits(
        execution_timeout=timedelta(seconds=60),
        node_timeout=timedelta(seconds=10),
        max_node_attempts=3,
    )
    metrics = EngineMetrics()
    project_id, version = await h.publish(base_catalog(Probe()), chain(2))
    with pytest.raises(ResourceLimitError) as caught:
        await service(h, lim, metrics).create_execution(project_id, version, **policies)
    assert caught.value.limit is limit
    assert await stored_executions(h) == 0
    assert rejected(metrics, limit) == 1


async def test_policies_exactly_at_the_caps_are_admitted(h: Harness) -> None:
    lim = limits(
        execution_timeout=timedelta(seconds=60),
        node_timeout=timedelta(seconds=10),
        max_node_attempts=3,
    )
    project_id, version = await h.publish(base_catalog(Probe()), chain(2))
    at = TimeoutPolicy(timedelta(seconds=60), timedelta(seconds=10))
    created = await service(h, lim).create_execution(
        project_id, version, timeout_policy=at, retry_policy=RetryPolicy(max_attempts=3)
    )
    persisted = await h.load(created.id)
    assert persisted.timeout_policy == at
    assert persisted.retry_policy.max_attempts == 3


# -- admission: graph -------------------------------------------------------------


@pytest.mark.parametrize(
    ("lim", "limit"),
    [
        (limits(max_graph_nodes=4), Limit.GRAPH_NODES),
        (limits(max_graph_depth=4), Limit.GRAPH_DEPTH),
    ],
)
async def test_graph_caps_at_and_beyond_the_boundary(
    h: Harness, lim: ResourceLimits, limit: Limit
) -> None:
    catalog = base_catalog(Probe())
    at = await run(h, catalog, chain(4), lim)
    assert at.status is _E.COMPLETED

    project_id, version = await h.publish(catalog, chain(5))
    with pytest.raises(ResourceLimitError) as caught:
        await service(h, lim).create_execution(project_id, version)
    assert caught.value.limit is limit
    assert await stored_executions(h) == 1  # only the admitted one


# -- admission: active executions per project ------------------------------------


async def test_project_capacity_at_beyond_and_after_one_finishes(h: Harness) -> None:
    lim = limits(max_active_executions_per_project=2)
    metrics = EngineMetrics()
    svc = service(h, lim, metrics)
    catalog = base_catalog(Probe())
    project_id, version = await h.publish(catalog, chain(2))
    other_id, other_version = await h.publish(catalog, chain(2))

    first = await svc.create_execution(project_id, version)
    await svc.create_execution(project_id, version)  # exactly at the cap
    with pytest.raises(ExecutionCapacityError):
        await svc.create_execution(project_id, version)
    assert rejected(metrics, Limit.ACTIVE_EXECUTIONS) == 1
    # The cap is per project.
    await svc.create_execution(other_id, other_version)

    # A finished (here: cancelled) execution frees its slot.
    await h.runtime(catalog).cancel(first.id)
    await svc.create_execution(project_id, version)
    with pytest.raises(ExecutionCapacityError):
        await svc.create_execution(project_id, version)
    assert await stored_executions(h) == 4


async def test_concurrent_admissions_never_exceed_the_capacity(h: Harness) -> None:
    """Stress: 20 simultaneous requests against a cap of 5. The project-row
    lock serializes them, so exactly 5 are admitted, never more."""
    svc = service(h, limits(max_active_executions_per_project=5))
    project_id, version = await h.publish(base_catalog(Probe()), chain(2))

    async with asyncio.timeout(GUARD):
        results = await asyncio.gather(
            *(svc.create_execution(project_id, version) for _ in range(20)),
            return_exceptions=True,
        )

    admitted = [r for r in results if isinstance(r, Execution)]
    refused = [r for r in results if isinstance(r, ExecutionCapacityError)]
    assert (len(admitted), len(refused)) == (5, 15)
    assert await stored_executions(h) == 5


# -- runtime: node output --------------------------------------------------------


async def test_node_output_exactly_at_the_limit_completes(h: Harness) -> None:
    probe = Probe()
    catalog = base_catalog(probe)
    graph, ids = build(
        {"A": ("test.emit", {"value": x(100)}), "B": ("test.append", {"suffix": ""})},
        ["A.out>B.text"],
    )
    result = await run(h, catalog, graph, limits(max_node_output_bytes=100))
    assert result.status is _E.COMPLETED
    assert result.node(ids["A"]).outputs == {"out": x(100)}


async def test_node_output_one_byte_over_fails_and_is_persisted(h: Harness) -> None:
    probe = Probe()
    catalog = base_catalog(probe)
    graph, ids = build(
        {
            "A": ("test.emit", {"value": x(101)}),
            "B": ("test.append", {"suffix": "!"}),
        },
        ["A.out>B.text"],
    )
    metrics = EngineMetrics()
    result = await run(
        h, catalog, graph, limits(max_node_output_bytes=100), metrics=metrics
    )

    persisted = await h.load(result.id)
    assert persisted.status is _E.FAILED
    assert persisted.error is not None
    assert (persisted.error.code, persisted.error.node_id) == (
        "NODE_OUTPUT_TOO_LARGE",
        ids["A"],
    )
    assert persisted.error.details == {"size": "101", "limit": "100"}
    a = persisted.node(ids["A"])
    assert a.status is _N.FAILED
    assert a.outputs is None  # the oversized output was never stored
    assert persisted.node(ids["B"]).status is _N.CANCELLED
    assert ids["B"] not in probe.started
    assert rejected(metrics, Limit.NODE_OUTPUT) == 1


async def test_output_limit_is_never_retried(h: Harness) -> None:
    """Combined with a retry policy: the limit is a permanent failure."""
    probe = Probe()
    catalog = base_catalog(probe)
    graph, ids = build(
        {"A": ("test.emit", {"value": x(101)}), "B": ("test.append", {"suffix": ""})},
        ["A.out>B.text"],
    )
    result = await run(
        h,
        catalog,
        graph,
        limits(max_node_output_bytes=100),
        retry_policy=RetryPolicy(max_attempts=3, initial_backoff=timedelta(0)),
    )
    assert result.status is _E.FAILED
    assert probe.started.count(ids["A"]) == 1
    assert result.node(ids["A"]).attempt == 1


# -- runtime: execution output ------------------------------------------------------


def a_then_b(probe: Probe) -> tuple[NodeCatalog, Graph, dict[str, str]]:
    """A emits 60 bytes; B copies them: 120 bytes in total."""
    catalog = base_catalog(probe)
    graph, ids = build(
        {
            "A": ("test.emit", {"value": x(60)}),
            "B": ("test.append", {"suffix": ""}),
        },
        ["A.out>B.text"],
    )
    return catalog, graph, ids


async def test_execution_output_exactly_at_the_limit_completes(h: Harness) -> None:
    catalog, graph, _ = a_then_b(Probe())
    lim = limits(max_node_output_bytes=60, max_execution_output_bytes=120)
    assert (await run(h, catalog, graph, lim)).status is _E.COMPLETED


async def test_execution_output_one_byte_over_fails_the_node_that_crossed_it(
    h: Harness,
) -> None:
    catalog, graph, ids = a_then_b(Probe())
    metrics = EngineMetrics()
    lim = limits(max_node_output_bytes=60, max_execution_output_bytes=119)
    result = await run(h, catalog, graph, lim, metrics=metrics)

    persisted = await h.load(result.id)
    assert persisted.status is _E.FAILED
    assert persisted.error is not None
    assert (persisted.error.code, persisted.error.node_id) == (
        "EXECUTION_OUTPUT_TOO_LARGE",
        ids["B"],
    )
    assert persisted.error.details == {"size": "120", "limit": "119"}
    assert persisted.node(ids["A"]).status is _N.COMPLETED  # truthful
    assert persisted.node(ids["B"]).outputs is None
    assert rejected(metrics, Limit.EXECUTION_OUTPUT) == 1


async def test_parallel_outputs_share_one_execution_budget(h: Harness) -> None:
    """Stress: a root fans out to 10 parallel nodes of 50 bytes each, with a
    concurrency of 4 and room for 6 of them after the root's output. The 7th
    recorded fails the run, and the rest never start or are stopped. No
    output beyond the budget is stored."""
    probe = Probe()

    async def pad(ctx: NodeExecutionContext) -> Mapping[str, Any]:
        probe.enter(ctx)
        return {"out": ctx.config["value"]}

    catalog = {
        **base_catalog(probe),
        "test.pad": node_type("test.pad", pad, inputs=text_in()),
    }
    fan = {f"N{i}": ("test.pad", {"value": x(50)}) for i in range(10)}
    graph, _ = build(
        {"R": ("test.emit", {"value": "r"}), **fan}, [f"R.out>{n}.text" for n in fan]
    )
    root = json_size({"out": "r"})
    lim = limits(max_node_output_bytes=50, max_execution_output_bytes=root + 300)
    project_id, version = await h.publish(catalog, graph)
    queued = await service(h, lim).create_execution(project_id, version)
    async with asyncio.timeout(GUARD):
        await h.runtime(catalog, limits=lim, max_concurrency=4).run(queued.id)

    persisted = await h.load(queued.id)
    assert persisted.status is _E.FAILED
    assert persisted.error is not None
    assert persisted.error.code == "EXECUTION_OUTPUT_TOO_LARGE"
    completed = [
        ne for ne in persisted.node_executions.values() if ne.status is _N.COMPLETED
    ]
    stored = sum(json_size(ne.outputs) for ne in completed)
    assert len(completed) == 1 + 6
    assert stored == root + 300


# -- durations at runtime -----------------------------------------------------------


def nap_then_hang(probe: Probe) -> tuple[NodeCatalog, Graph, dict[str, str]]:
    """A runs 0.15s; B never finishes on its own. With both caps at 0.3s,
    B's own deadline (~0.45s) lies after the execution's, so the execution
    cap is what fires."""

    async def nap(ctx: NodeExecutionContext) -> Mapping[str, Any]:
        probe.enter(ctx)
        await asyncio.sleep(0.15)
        return {"out": "a"}

    catalog = {
        "test.nap": node_type("test.nap", nap),
        "test.gated": gated("test.gated", probe, asyncio.Event(), never_set()),
    }
    graph, ids = build(
        {"A": ("test.nap", {}), "B": ("test.gated", {})}, ["A.out>B.text"]
    )
    return catalog, graph, ids


THIRD = limits(
    execution_timeout=timedelta(seconds=0.3), node_timeout=timedelta(seconds=0.3)
)


async def test_execution_duration_cap_applies_without_a_policy(h: Harness) -> None:
    """No timeout policy requested: the engine cap still ends the run, and
    the TIMEOUT is persisted."""
    probe = Probe()
    catalog, graph, ids = nap_then_hang(probe)
    result = await run(h, catalog, graph, THIRD)

    persisted = await h.load(result.id)
    assert persisted.status is _E.TIMEOUT
    assert persisted.node(ids["A"]).status is _N.COMPLETED
    assert persisted.node(ids["B"]).status is _N.CANCELLED
    assert probe.cleaned == [f"{ids['B']}:True"]  # node code really stopped


async def test_node_duration_cap_applies_without_a_policy(h: Harness) -> None:
    probe = Probe()
    catalog = {
        **base_catalog(probe),
        "test.gated": gated("test.gated", probe, asyncio.Event(), never_set()),
    }
    graph, ids = build(
        {"A": ("test.emit", {"value": "a"}), "B": ("test.gated", {})},
        ["A.out>B.text"],
    )
    lim = limits(
        execution_timeout=timedelta(seconds=GUARD), node_timeout=timedelta(seconds=0.2)
    )
    result = await run(h, catalog, graph, lim)

    persisted = await h.load(result.id)
    assert persisted.status is _E.FAILED
    assert persisted.error is not None
    assert (persisted.error.code, persisted.error.node_id) == ("NODE_TIMEOUT", ids["B"])
    assert persisted.node(ids["B"]).status is _N.FAILED


# -- cancellation after a limit -------------------------------------------------------


async def test_cancel_after_a_limit_ended_the_run_is_rejected(h: Harness) -> None:
    catalog = base_catalog(Probe())
    graph, _ = build(
        {"A": ("test.emit", {"value": x(101)}), "B": ("test.append", {"suffix": ""})},
        ["A.out>B.text"],
    )
    failed = await run(h, catalog, graph, limits(max_node_output_bytes=100))
    before = await h.load(failed.id)

    with pytest.raises(InvalidStateTransitionError):
        await h.runtime(catalog).cancel(failed.id, reason="too late")

    after = await h.load(failed.id)
    assert after.status is _E.FAILED
    assert after.cancellation is None
    assert after.revision == before.revision


async def test_cancel_after_a_timeout_is_rejected(h: Harness) -> None:
    catalog, graph, _ = nap_then_hang(Probe())
    timed_out = await run(h, catalog, graph, THIRD)
    assert timed_out.status is _E.TIMEOUT
    before = await h.load(timed_out.id)

    with pytest.raises(InvalidStateTransitionError):
        await h.runtime(catalog).cancel(timed_out.id)
    after = await h.load(timed_out.id)
    assert (after.status, after.revision) == (_E.TIMEOUT, before.revision)


# -- resume keeps the budget ----------------------------------------------------


async def test_resumed_run_counts_output_produced_before_the_crash(
    h: Harness,
) -> None:
    """A produced 60 bytes before the owner died. After recovery B's 61 bytes
    would fit a fresh budget of 119, but not the remaining 59. The budget must
    survive the crash."""
    probe = Probe()
    started, release = asyncio.Event(), asyncio.Event()
    catalog = {
        **base_catalog(probe),
        "test.gated": gated("test.gated", probe, started, release),
    }
    graph, ids = build(
        {"A": ("test.emit", {"value": x(60)}), "B": ("test.gated", {})},
        ["A.out>B.text"],
    )
    lim = limits(max_node_output_bytes=100, max_execution_output_bytes=119)
    project_id, version = await h.publish(catalog, graph)
    queued = await service(h, lim).create_execution(project_id, version)

    owner = asyncio.create_task(h.runtime(catalog, limits=lim).run(queued.id))
    await wait_for(started)
    await crash(owner)
    await make_stale(h, queued.id)

    restarted = h.runtime(catalog, limits=lim)
    [recovered] = await restarted.recover_stale(stale_after=STALE)
    assert recovered.status is _E.QUEUED
    release.set()
    async with asyncio.timeout(GUARD):
        await restarted.run(queued.id)

    persisted = await eventually(h, queued.id, lambda e: e.is_terminal)
    assert persisted.status is _E.FAILED
    assert persisted.error is not None
    assert (persisted.error.code, persisted.error.node_id) == (
        "EXECUTION_OUTPUT_TOO_LARGE",
        ids["B"],
    )
    assert probe.started.count(ids["A"]) == 1  # finished work not redone
    assert persisted.node(ids["A"]).status is _N.COMPLETED


# -- concurrency -----------------------------------------------------------------


async def test_in_flight_nodes_never_exceed_the_concurrency_limit(h: Harness) -> None:
    """Stress: 24 independent nodes, concurrency 3. The peak of
    simultaneously running node code is exactly the limit."""
    in_flight = peak = 0

    async def slow(ctx: NodeExecutionContext) -> Mapping[str, Any]:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return {"out": "ok"}

    catalog = {
        **base_catalog(Probe()),
        "test.slow": node_type("test.slow", slow, inputs=text_in()),
    }
    fan: dict[str, tuple[str, dict[str, Any]]] = {
        f"N{i:02}": ("test.slow", {}) for i in range(24)
    }
    graph, _ = build(
        {"R": ("test.emit", {"value": "r"}), **fan}, [f"R.out>{n}.text" for n in fan]
    )
    lim = limits()
    project_id, version = await h.publish(catalog, graph)
    queued = await service(h, lim).create_execution(project_id, version)
    async with asyncio.timeout(GUARD):
        result = await h.runtime(catalog, limits=lim, max_concurrency=3).run(queued.id)
    assert result.status is _E.COMPLETED
    assert peak == 3
