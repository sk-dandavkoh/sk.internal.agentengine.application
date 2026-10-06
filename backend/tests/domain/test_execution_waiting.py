"""Waiting nodes in the Execution aggregate: wait, answer, expire, cancel.

The generic legal/illegal matrices and the stateful fuzzer in
test_execution.py cover the transition tables; these tests pin the rules
around them (validation, duplicate answers, deadlines, events)."""

from datetime import timedelta

import pytest

from akasha.domain.event import EventType
from akasha.domain.execution import (
    Execution,
    ExecutionStatus,
    NodeExecution,
    NodeExecutionStatus,
    TimeoutPolicy,
)
from akasha.errors import DomainValidationError, InvalidStateTransitionError
from tests.domain.test_execution import T0, WAIT, _ids, _run_node, _running

E = ExecutionStatus
N = NodeExecutionStatus
S = timedelta(seconds=1)


def _waiting(
    n_nodes: int = 2, timeout_policy: TimeoutPolicy | None = None
) -> tuple[Execution, str]:
    e = _running(n_nodes, timeout_policy=timeout_policy)
    a = _ids(e)[0]
    _run_node(e, a)
    e.wait_node(a, WAIT, now=T0)
    e.wait(now=T0)
    return e, a


def test_waiting_execution_records_the_request_and_events() -> None:
    e, a = _waiting()
    assert (e.status, e.node(a).status) == (E.WAITING, N.WAITING)
    assert e.node(a).waiting_for == WAIT
    types = [ev.event_type for ev in e.pull_events()]
    assert types[-2:] == [EventType.NODE_WAITING, EventType.EXECUTION_WAITING]


def test_answer_makes_the_node_ready_and_queues_the_execution() -> None:
    e, a = _waiting()
    e.pull_events()
    e.resume_node(a, {"decision": "approve"}, responded_by="usr_1", now=T0)
    assert (e.status, e.node(a).status) == (E.QUEUED, N.READY)
    assert e.is_resumed  # a worker resumes it like a recovered run
    assert e.node(a).resume_input == {
        "response": {"decision": "approve"},
        "responded_by": "usr_1",
        "timed_out": False,
    }
    (event,) = e.pull_events()
    assert event.event_type is EventType.NODE_RESUMED
    assert event.payload == {"outcome": "responded"}  # never the answer itself


@pytest.mark.parametrize(
    "response", [{}, {"decision": "maybe"}, {"decision": "approve", "extra": 1}]
)
def test_answer_must_match_the_request(response: dict[str, object]) -> None:
    e, a = _waiting()
    with pytest.raises(DomainValidationError):
        e.resume_node(a, response)
    assert (e.status, e.node(a).status) == (E.WAITING, N.WAITING)


def test_second_answer_is_refused() -> None:
    e, a = _waiting()
    e.resume_node(a, {"decision": "approve"})
    with pytest.raises(InvalidStateTransitionError):
        e.resume_node(a, {"decision": "reject"})
    resume = e.node(a).resume_input
    assert resume is not None and resume["response"] == {"decision": "approve"}


def test_answer_to_a_node_that_is_not_waiting_is_refused() -> None:
    e, _ = _waiting()
    other = _ids(e)[1]
    with pytest.raises(InvalidStateTransitionError):
        e.resume_node(other, {"decision": "approve"})


def test_answer_after_cancellation_request_is_refused() -> None:
    e, a = _waiting()
    e.request_cancellation(now=T0)
    with pytest.raises(InvalidStateTransitionError):
        e.resume_node(a, {"decision": "approve"})


def test_cancel_while_waiting_cancels_the_waiting_node() -> None:
    e, a = _waiting()
    e.cancel(now=T0)
    assert (e.status, e.node(a).status) == (E.CANCELLED, N.CANCELLED)


def test_wake_at_is_the_earliest_deadline() -> None:
    e, _ = _waiting(
        timeout_policy=TimeoutPolicy(execution_timeout=timedelta(seconds=10))
    )
    assert e.wake_at == T0 + 10 * S  # earlier than the node's 30 s wait
    e2, _ = _waiting()
    assert e2.wake_at == WAIT.deadline
    e2.resume_node(_ids(e2)[0], {"decision": "approve"})
    assert e2.wake_at is None  # only WAITING executions wake


def test_expired_wait_resumes_the_node_as_timed_out() -> None:
    e, a = _waiting()
    assert WAIT.deadline is not None
    assert not e.expire_waits(now=WAIT.deadline - S)  # not due yet
    assert e.status is E.WAITING
    assert e.expire_waits(now=WAIT.deadline)
    assert (e.status, e.node(a).status) == (E.QUEUED, N.READY)
    assert e.node(a).resume_input == {
        "response": None,
        "responded_by": None,
        "timed_out": True,
    }


def test_execution_deadline_while_waiting_times_out() -> None:
    e, a = _waiting(
        timeout_policy=TimeoutPolicy(execution_timeout=timedelta(seconds=10))
    )
    assert e.expire_waits(now=T0 + 10 * S)
    assert e.status is E.TIMEOUT
    assert e.node(a).status is N.CANCELLED


def test_waiting_state_survives_rehydration() -> None:
    e, _ = _waiting()
    copy = Execution(
        id=e.id,
        workflow_id=e.workflow_id,
        workflow_version=e.workflow_version,
        status=e.status,
        node_executions=e.node_executions,
        started_at=e.started_at,
        revision=1,
    )
    assert copy.wake_at == e.wake_at


def test_a_waiting_node_without_its_request_cannot_be_loaded() -> None:
    with pytest.raises(DomainValidationError):
        NodeExecution(
            id="nexec_" + "1" * 32,
            node_id="node_" + "1" * 32,
            status=N.WAITING,
            attempt=1,
        )


def test_answering_continues_the_attempt_that_asked() -> None:
    e, a = _waiting()
    e.resume_node(a, {"decision": "approve"})
    e.start(now=T0 + S)
    e.start_node(a, now=T0 + S)
    assert e.node(a).attempt == 1  # no phantom retry, no retry budget used
    e.complete_node(a, outputs={}, now=T0 + S)
    assert e.node(a).failures == []
