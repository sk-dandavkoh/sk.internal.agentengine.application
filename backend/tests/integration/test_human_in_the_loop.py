"""Human-in-the-loop against real PostgreSQL with the real built-in nodes.

The central proof (``test_waiting_execution_survives_a_restart_and_resumes``):

    workflow starts → core.human is reached → execution WAITING (persisted,
    ``run`` returned: no task, no worker slot held) → the runtime and its
    database engine are thrown away ("process restart") → a fresh runtime
    finds it still WAITING and does not treat it as stale → an operator
    answers → it is queued → a worker resumes it → downstream nodes run →
    COMPLETED with the right branch.

No sleeps: waits are ended by answers or by an injected clock passing the
deadline (``expire_waits``). The HTTP layer and the queue/worker path are
covered by tests/integration/test_core_portfolio_e2e.py.
"""

import asyncio
from datetime import timedelta
from typing import Any

import pytest

from akasha.application.persistence import ConcurrentModificationError
from akasha.domain.event import EventType
from akasha.domain.execution import ExecutionStatus, NodeExecutionStatus, TimeoutPolicy
from akasha.errors import DomainValidationError, InvalidStateTransitionError
from akasha.infrastructure.database import Database, normalize_async_url
from akasha.nodes.builtins import create_builtin_registry
from tests.integration.test_runtime import GUARD, Harness, build

pytestmark = pytest.mark.usefixtures("clean_db")

_E = ExecutionStatus
_N = NodeExecutionStatus
CATALOG = create_builtin_registry().catalog
LEAD = {"name": "Ada", "score": 91}


def approval_graph(**human: Any) -> tuple[Any, dict[str, str]]:
    return build(
        {
            "lead": ("core.input", {"field": "lead"}),
            "review": ("core.human", {"prompt": "Contact {{ value.name }}?"} | human),
            "message": (
                "core.transform",
                {
                    "steps": [
                        {"op": "template", "template": "Contacting {{ value.name }}"}
                    ]
                },
            ),
            "sent": ("core.output", {}),
            "dropped": ("core.output", {}),
            "expired": ("core.output", {}),
            "audit": ("core.output", {}),
        },
        [
            "lead.value>review.value",
            "review.approved>message.value",
            "message.value>sent.value",
            "review.rejected>dropped.value",
            "review.timed_out>expired.value",
            "review.response>audit.value",
        ],
    )


async def start(
    h: Harness, graph: Any, *, timeout_policy: TimeoutPolicy | None = None
) -> str:
    workflow_id, version = await h.publish(CATALOG, graph)
    execution = await h.executions.create_execution(
        workflow_id, version, input={"lead": LEAD}, timeout_policy=timeout_policy
    )
    async with asyncio.timeout(GUARD):
        ran = await h.runtime(CATALOG).run(execution.id)
    assert ran.status is _E.WAITING  # ``run`` returned instead of blocking
    return execution.id


async def test_waiting_execution_survives_a_restart_and_resumes(
    database: Database, db_url: str
) -> None:
    h = Harness(database)
    graph, ids = approval_graph()
    execution_id = await start(h, graph)

    # Persisted: the request, and nothing downstream has run.
    stored = await h.load(execution_id)
    review = stored.node(ids["review"])
    assert (stored.status, review.status) == (_E.WAITING, _N.WAITING)
    assert review.waiting_for is not None
    assert review.waiting_for.prompt == "Contact Ada?"
    assert stored.node(ids["message"]).status is _N.PENDING

    # "Process restart": a new database engine and a new runtime. Waiting
    # executions have no owner, so stale recovery leaves them alone.
    restarted_db = Database(normalize_async_url(db_url))
    try:
        h2 = Harness(restarted_db, clock=h.clock)
        assert await h2.runtime(CATALOG).recover_stale(stale_after=timedelta(0)) == []
        assert (await h2.load(execution_id)).status is _E.WAITING

        pending = await h2.executions.pending_inputs(execution_id)
        assert list(pending) == [ids["review"]]
        assert pending[ids["review"]].response_schema["required"] == ["decision"]

        answered = await h2.executions.respond(
            execution_id,
            ids["review"],
            {"decision": "approve", "comment": "go ahead"},
            responded_by="usr_operator",
        )
        assert answered.status is _E.QUEUED

        async with asyncio.timeout(GUARD):
            done = await h2.runtime(CATALOG).run(execution_id)
    finally:
        await restarted_db.dispose()

    assert done.status is _E.COMPLETED
    final = await h.load(execution_id)
    assert final.node(ids["sent"]).outputs == {"value": "Contacting Ada"}
    assert final.node(ids["dropped"]).status is _N.SKIPPED
    assert final.node(ids["expired"]).status is _N.SKIPPED
    assert final.node(ids["audit"]).outputs == {
        "value": {
            "decision": "approve",
            "comment": "go ahead",
            "responded_by": "usr_operator",
        }
    }
    assert final.node(ids["review"]).attempt == 1  # answering is no retry
    async with h.uow() as uow:
        events = await uow.events.list(execution_id=execution_id, after=0, limit=200)
    types = [e.event_type for e in events]
    for later, earlier in (
        (EventType.EXECUTION_WAITING, EventType.NODE_WAITING),
        (EventType.NODE_RESUMED, EventType.EXECUTION_WAITING),
        (EventType.EXECUTION_RESUMED, EventType.NODE_RESUMED),
        (EventType.EXECUTION_COMPLETED, EventType.EXECUTION_RESUMED),
    ):
        assert types.index(earlier) < types.index(later)


async def test_rejection_takes_the_rejected_branch(database: Database) -> None:
    h = Harness(database)
    graph, ids = approval_graph()
    execution_id = await start(h, graph)
    await h.executions.respond(execution_id, ids["review"], {"decision": "reject"})
    done = await h.runtime(CATALOG).run(execution_id)
    assert done.status is _E.COMPLETED
    assert done.node(ids["dropped"]).outputs == {"value": LEAD}
    assert done.node(ids["sent"]).status is _N.SKIPPED


async def test_invalid_and_duplicate_answers_are_refused(database: Database) -> None:
    h = Harness(database)
    graph, ids = approval_graph()
    execution_id = await start(h, graph)

    with pytest.raises(DomainValidationError):
        await h.executions.respond(execution_id, ids["review"], {"decision": "maybe"})
    assert (await h.load(execution_id)).status is _E.WAITING

    # Two operators answer at once: exactly one wins (compare-and-swap).
    results = await asyncio.gather(
        h.executions.respond(execution_id, ids["review"], {"decision": "approve"}),
        h.executions.respond(execution_id, ids["review"], {"decision": "reject"}),
        return_exceptions=True,
    )
    failures = [r for r in results if isinstance(r, Exception)]
    assert len(failures) == 1
    assert isinstance(
        failures[0], InvalidStateTransitionError | ConcurrentModificationError
    )
    with pytest.raises(InvalidStateTransitionError):  # and later ones too
        await h.executions.respond(execution_id, ids["review"], {"decision": "reject"})


async def test_wait_deadline_continues_on_timed_out(database: Database) -> None:
    h = Harness(database)
    graph, ids = approval_graph(timeout_seconds=60)
    execution_id = await start(h, graph)
    runtime = h.runtime(CATALOG)

    assert await runtime.expire_waits() == []  # not due yet
    h.clock.now += timedelta(seconds=120)
    (expired,) = await runtime.expire_waits()
    assert expired.status is _E.QUEUED
    assert await runtime.expire_waits() == []  # settled once

    done = await runtime.run(execution_id)
    assert done.status is _E.COMPLETED
    assert done.node(ids["expired"]).outputs == {"value": LEAD}
    assert done.node(ids["audit"]).status is _N.SKIPPED  # nobody answered


async def test_execution_deadline_ends_a_waiting_execution(database: Database) -> None:
    h = Harness(database)
    graph, ids = approval_graph()
    execution_id = await start(
        h, graph, timeout_policy=TimeoutPolicy(execution_timeout=timedelta(minutes=5))
    )
    h.clock.now += timedelta(minutes=10)
    (timed_out,) = await h.runtime(CATALOG).expire_waits()
    assert timed_out.status is _E.TIMEOUT
    stored = await h.load(execution_id)
    assert stored.status is _E.TIMEOUT
    assert stored.node(ids["review"]).status is _N.CANCELLED


async def test_cancelling_a_waiting_execution(database: Database) -> None:
    h = Harness(database)
    graph, ids = approval_graph()
    execution_id = await start(h, graph)
    await h.runtime(CATALOG).cancel(execution_id, reason="not needed")
    stored = await h.load(execution_id)
    assert stored.status is _E.CANCELLED
    assert stored.node(ids["review"]).status is _N.CANCELLED
    with pytest.raises(InvalidStateTransitionError):
        await h.executions.respond(execution_id, ids["review"], {"decision": "approve"})


async def test_parallel_branches_finish_before_the_execution_waits(
    database: Database,
) -> None:
    """A wait parks the execution only once nothing else can run: the
    independent branch completes in the same run."""
    h = Harness(database)
    graph, ids = build(
        {
            "lead": ("core.input", {"field": "lead"}),
            "review": ("core.human", {"prompt": "ok?"}),
            "side": (
                "core.transform",
                {"steps": [{"op": "pick", "path": "name"}]},
            ),
            "side_out": ("core.output", {}),
            "after": ("core.output", {}),
        },
        [
            "lead.value>review.value",
            "lead.value>side.value",
            "side.value>side_out.value",
            "review.approved>after.value",
        ],
    )
    execution_id = await start(h, graph)
    stored = await h.load(execution_id)
    assert stored.node(ids["side_out"]).outputs == {"value": "Ada"}
    assert stored.node(ids["after"]).status is _N.PENDING
    await h.executions.respond(execution_id, ids["review"], {"decision": "approve"})
    done = await h.runtime(CATALOG).run(execution_id)
    assert done.status is _E.COMPLETED
    assert done.node(ids["after"]).outputs == {"value": LEAD}
    assert done.node(ids["side"]).attempt == 1  # finished work never reruns
