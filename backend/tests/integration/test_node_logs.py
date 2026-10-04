"""Node log lines through the real runtime and PostgreSQL (frontend Phase 15).

What a node writes through ``context.logger`` is stored per attempt in the
same save as the attempt's outcome: retries keep every attempt's lines,
failed and cancelled attempts keep theirs, and the per-attempt bound holds.
"""

import asyncio
import logging
from collections.abc import Iterator, Mapping
from typing import Any

import pytest

from akasha.application.executions import ExecutionService
from akasha.domain.execution import (
    ExecutionStatus,
    LogLevel,
    NodeExecutionStatus,
    NodeLogEntry,
)
from akasha.domain.node import NodeError, NodeErrorKind, NodeExecutionContext
from akasha.infrastructure.database import Database
from akasha.runtime.node_logs import MAX_LINES_PER_ATTEMPT
from tests.integration.test_reliability import RETRY3, queue
from tests.integration.test_runtime import (
    GUARD,
    Harness,
    Probe,
    base_catalog,
    build,
    never_set,
    node_type,
    text_in,
    wait_for,
)

pytestmark = pytest.mark.usefixtures("clean_db")

_E = ExecutionStatus
_N = NodeExecutionStatus


@pytest.fixture
def h(database: Database) -> Harness:
    return Harness(database)


@pytest.fixture(autouse=True)
def _capture_info() -> Iterator[None]:
    # Records below the logger's effective level never reach a handler;
    # pin it so the tests do not depend on the process log level.
    nodes = logging.getLogger("akasha.nodes")
    nodes.setLevel(logging.DEBUG)
    yield
    nodes.setLevel(logging.NOTSET)


async def stored(h: Harness, execution_id: str, **filters: Any) -> list[NodeLogEntry]:
    async with h.uow() as uow:
        return list(await uow.node_logs.list(execution_id, limit=10_000, **filters))


async def test_every_attempt_keeps_its_lines_in_order(h: Harness) -> None:
    probe = Probe()
    catalog = base_catalog(probe)
    attempts: list[int] = []

    async def flaky(ctx: NodeExecutionContext) -> Mapping[str, Any]:
        attempts.append(1)
        ctx.logger.info("attempt %d calling upstream", len(attempts))
        if len(attempts) == 1:
            ctx.logger.warning("upstream busy", extra={"status": 503})
            raise NodeError("busy", kind=NodeErrorKind.TRANSIENT)
        return {"out": ctx.inputs["text"] + "!"}

    catalog["test.flaky"] = node_type("test.flaky", flaky, inputs=text_in())
    graph, ids = build(
        {"A": ("test.emit", {"value": "a"}), "B": ("test.flaky", {})}, ["A.out>B.text"]
    )
    queued = await queue(h, catalog, graph, retry_policy=RETRY3)

    result = await h.runtime(catalog).run(queued.id)

    assert result.status is _E.COMPLETED
    lines = await stored(h, queued.id)
    assert [(e.node_id, e.attempt, e.level, e.message, e.fields) for e in lines] == [
        (ids["B"], 1, LogLevel.INFO, "attempt 1 calling upstream", {}),
        (ids["B"], 1, LogLevel.WARNING, "upstream busy", {"status": 503}),
        (ids["B"], 2, LogLevel.INFO, "attempt 2 calling upstream", {}),
    ]
    positions = [e.position or 0 for e in lines]
    assert positions == sorted(positions)
    # The injected clock stamps lines: tests control time (AGENTS §25).
    assert all(e.logged_at.year == 2100 for e in lines)


async def test_failed_and_cancelled_attempts_keep_their_lines(h: Harness) -> None:
    probe = Probe()
    catalog = base_catalog(probe)
    started = asyncio.Event()

    async def failing(ctx: NodeExecutionContext) -> Mapping[str, Any]:
        ctx.logger.error("cannot parse %s", ctx.inputs["text"])
        raise NodeError("bad input", kind=NodeErrorKind.PERMANENT)

    async def waiting(ctx: NodeExecutionContext) -> Mapping[str, Any]:
        ctx.logger.info("waiting for approval")
        started.set()
        await never_set().wait()
        return {}

    catalog["test.failing"] = node_type("test.failing", failing, inputs=text_in())
    catalog["test.waiting"] = node_type("test.waiting", waiting, inputs=text_in())
    failed_graph, f_ids = build(
        {"A": ("test.emit", {"value": "x"}), "B": ("test.failing", {})},
        ["A.out>B.text"],
    )
    failed = await h.queue(catalog, failed_graph)
    assert (await h.runtime(catalog).run(failed.id)).status is _E.FAILED
    [line] = await stored(h, failed.id)
    assert (line.node_id, line.level, line.message) == (
        f_ids["B"],
        LogLevel.ERROR,
        "cannot parse x",
    )

    waiting_graph, w_ids = build(
        {"A": ("test.emit", {"value": "x"}), "W": ("test.waiting", {})},
        ["A.out>W.text"],
    )
    cancelled = await h.queue(catalog, waiting_graph)
    runtime = h.runtime(catalog)
    task = asyncio.create_task(runtime.run(cancelled.id))
    await wait_for(started)
    await runtime.cancel(cancelled.id, reason="stop")
    async with asyncio.timeout(GUARD):
        assert (await task).status is _E.CANCELLED
    [line] = await stored(h, cancelled.id)
    assert (line.node_id, line.message) == (w_ids["W"], "waiting for approval")


async def test_large_logs_are_bounded_per_attempt_and_paged(h: Harness) -> None:
    probe = Probe()
    catalog = base_catalog(probe)

    async def chatty(ctx: NodeExecutionContext) -> Mapping[str, Any]:
        for i in range(MAX_LINES_PER_ATTEMPT + 50):
            ctx.logger.debug("row %d", i)
        return {"out": "done"}

    catalog["test.chatty"] = node_type("test.chatty", chatty, inputs=text_in())
    graph, _ = build(
        {"A": ("test.emit", {"value": "x"}), "C": ("test.chatty", {})},
        ["A.out>C.text"],
    )
    queued = await h.queue(catalog, graph)
    await h.runtime(catalog).run(queued.id)

    svc = ExecutionService(h.uow)
    seen: list[NodeLogEntry] = []
    after = 0
    while True:
        page = await svc.list_logs(queued.id, after=after, limit=100)
        seen.extend(page.items)
        if not page.has_more:
            break
        after = seen[-1].position or 0
    assert len(seen) == MAX_LINES_PER_ATTEMPT + 1
    assert seen[-1].level is LogLevel.WARNING
    assert seen[-1].message.startswith("50 further log lines")
    warnings = await svc.list_logs(queued.id, min_level=LogLevel.WARNING)
    assert [e.message for e in warnings.items] == [seen[-1].message]
