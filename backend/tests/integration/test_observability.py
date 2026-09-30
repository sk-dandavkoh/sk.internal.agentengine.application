"""Execution observability against the real runtime and PostgreSQL (Phase 12).

Acceptance: a failed execution can be diagnosed from structured backend
observability data alone — which node failed, why, when, for how long, and
under which correlation id — without leaking secrets anywhere.
"""

import asyncio
import json
import logging
from collections.abc import Mapping
from typing import Any

import pytest
from sqlalchemy import text

from akasha.application.executions import ExecutionNotFoundError
from akasha.domain.execution import ExecutionStatus
from akasha.domain.node import NodeError, NodeErrorKind, NodeExecutionContext
from akasha.infrastructure.database import Database
from akasha.observability.metrics import EngineMetrics
from tests.integration.test_runtime import (
    Harness,
    Probe,
    base_catalog,
    build,
    node_type,
    text_in,
)
from tests.support import capture_json_logs

pytestmark = pytest.mark.usefixtures("clean_db")

CANARY = "sk-canary-0123456789abcdef"


@pytest.fixture
def h(database: Database) -> Harness:
    return Harness(database)


def sample(m: EngineMetrics, name: str, **labels: str) -> float | None:
    return m.registry.get_sample_value(name, labels)


def ms(seconds: float) -> int:
    return round(seconds * 1000)


async def test_failed_execution_is_diagnosable_from_logs_and_metrics(
    h: Harness, database: Database
) -> None:
    node_log = logging.getLogger("akasha.nodes.test.leaky")

    async def leaky(ctx: NodeExecutionContext) -> Mapping[str, Any]:
        node_log.info("calling upstream")  # node code: no ids passed by hand
        raise NodeError(
            f"upstream rejected api_key={CANARY}",
            kind=NodeErrorKind.PERMANENT,
            code="UPSTREAM_REJECTED",
            details={"authorization": f"Bearer {CANARY}", "status": "401"},
        )

    catalog = base_catalog(Probe())
    catalog["test.leaky"] = node_type("test.leaky", leaky, inputs=text_in())
    graph, ids = build(
        {
            "A": ("test.emit", {"value": "a"}),
            "B": ("test.leaky", {}),
            "C": ("test.append", {"suffix": "!"}),
        },
        ["A.out>B.text", "B.out>C.text"],
    )
    queued = await h.queue(catalog, graph)
    metrics = EngineMetrics()
    with capture_json_logs() as lines:
        await h.runtime(catalog, metrics=metrics).run(queued.id)
    loaded = await h.load(queued.id)
    assert loaded.status is ExecutionStatus.FAILED
    b = loaded.node(ids["B"])

    # Node code logged without passing any id: the runtime's context did.
    (inside,) = [ln for ln in lines if ln["message"] == "calling upstream"]
    assert inside["execution_id"] == queued.id
    assert inside["node_id"] == ids["B"]
    assert inside["node_execution_id"] == b.id
    assert inside["node_type"] == "test.leaky"
    assert inside["correlation_id"] == "corr-1"

    # The execution's story, in order, from the execution logger alone.
    story = [ln for ln in lines if ln["logger"] == "akasha.execution"]
    assert all(ln["execution_id"] == queued.id for ln in story)
    assert all(ln["correlation_id"] == "corr-1" for ln in story)
    assert [(ln["event"], ln.get("node_id")) for ln in story] == [
        ("execution.started", None),
        ("node.started", ids["A"]),
        ("node.completed", ids["A"]),
        ("node.started", ids["B"]),
        ("node.failed", ids["B"]),
        ("node.cancelled", ids["C"]),
        ("execution.failed", None),
    ]
    failed_node = story[4]
    assert failed_node["level"] == "WARNING"
    assert failed_node["node_execution_id"] == b.id
    assert failed_node["error_code"] == "UPSTREAM_REJECTED"
    assert failed_node["error_kind"] == "permanent"
    assert failed_node["error_message"] == "upstream rejected api_key=[REDACTED]"
    assert b.started_at and b.finished_at
    assert failed_node["duration_ms"] == ms(
        (b.finished_at - b.started_at).total_seconds()
    )
    failed = story[-1]
    assert failed["error_node_id"] == ids["B"]
    assert loaded.started_at and loaded.finished_at
    assert failed["duration_ms"] == ms(
        (loaded.finished_at - loaded.started_at).total_seconds()
    )
    assert story[0]["queue_wait_ms"] == ms(
        (loaded.started_at - loaded.created_at).total_seconds()
    )

    # Metrics tell the same story.
    assert sample(metrics, "akasha_executions_started_total") == 1
    assert sample(metrics, "akasha_executions_finished_total", status="failed") == 1
    assert sample(metrics, "akasha_active_executions") == 0
    assert (
        sample(
            metrics,
            "akasha_node_execution_errors_total",
            node_type="test.leaky",
            error_kind="permanent",
        )
        == 1
    )
    assert (
        sample(
            metrics,
            "akasha_node_executions_finished_total",
            node_type="test.append",
            status="cancelled",
        )
        == 1
    )

    # The secret reached neither the logs nor the persisted error.
    assert CANARY not in json.dumps(lines)
    error = b.error
    assert error is not None
    assert error.message == "upstream rejected api_key=[REDACTED]"
    assert error.details == {"authorization": "[REDACTED]", "status": "401"}
    async with database.engine.connect() as conn:
        stored: list[str] = list(
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
    assert len(stored) == 3
    assert all(CANARY not in row for row in stored)


async def test_active_executions_gauge_tracks_concurrent_runs(h: Harness) -> None:
    started = [asyncio.Event(), asyncio.Event()]
    release = asyncio.Event()

    async def wait(ctx: NodeExecutionContext) -> Mapping[str, Any]:
        started[int(ctx.config["i"])].set()
        await release.wait()
        return {"out": "done"}

    catalog = base_catalog(Probe())
    catalog["test.wait"] = node_type("test.wait", wait, inputs=text_in())
    metrics = EngineMetrics()
    runtime = h.runtime(catalog, metrics=metrics)
    runs = []
    for i in (0, 1):
        graph, _ = build(
            {"A": ("test.emit", {"value": "a"}), "W": ("test.wait", {"i": i})},
            ["A.out>W.text"],
        )
        queued = await h.queue(catalog, graph)
        runs.append(asyncio.create_task(runtime.run(queued.id)))

    async with asyncio.timeout(10):
        await asyncio.gather(*(e.wait() for e in started))
    assert sample(metrics, "akasha_active_executions") == 2
    release.set()
    async with asyncio.timeout(10):
        results = await asyncio.gather(*runs)
    assert [r.status for r in results] == [ExecutionStatus.COMPLETED] * 2
    assert sample(metrics, "akasha_active_executions") == 0
    assert sample(metrics, "akasha_executions_finished_total", status="completed") == 2


async def test_active_executions_gauge_is_released_when_a_run_errors(
    h: Harness,
) -> None:
    metrics = EngineMetrics()
    runtime = h.runtime(base_catalog(Probe()), metrics=metrics)
    with pytest.raises(ExecutionNotFoundError):
        await runtime.run(f"exec_{'0' * 32}")
    assert sample(metrics, "akasha_active_executions") == 0
