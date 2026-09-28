"""Execution + NodeExecution state machines (roadmap Phase 07, AGENTS §23).

Layers:
1. Spec   — the full legal transition set written out independently of the
            implementation; any table change must change this file.
2. Matrix — every (state, action) pair, legal and illegal, table-driven.
3. Scenarios — lifecycle, failure, retry, timeout, cancellation, terminal.
4. Stateful fuzz — Hypothesis drives random action sequences and checks the
            invariants after every step (rejections must not mutate).
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from akasha import __version__
from akasha.domain.execution import (
    EXECUTION_ACTIONS,
    EXECUTION_TRANSITIONS,
    NODE_ACTIONS,
    NODE_TRANSITIONS,
    TERMINAL_EXECUTION_STATUSES,
    TERMINAL_NODE_STATUSES,
    CancellationState,
    Execution,
    ExecutionError,
    ExecutionMetadata,
    ExecutionStatus,
    NodeExecution,
    NodeExecutionStatus,
    RetryPolicy,
    TimeoutPolicy,
)
from akasha.domain.graph import Graph, NodeInstance
from akasha.domain.node import NodeError, NodeErrorKind
from akasha.domain.project import Project, ProjectVersion
from akasha.domain.trigger import TriggerType
from akasha.errors import DomainValidationError, InvalidStateTransitionError

E = ExecutionStatus
N = NodeExecutionStatus
T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
S = timedelta(seconds=1)


def _err(
    kind: NodeErrorKind = NodeErrorKind.PERMANENT,
    *,
    node_id: str | None = None,
    details: dict[str, str] | None = None,
) -> ExecutionError:
    return ExecutionError(
        code="NODE_EXECUTION_ERROR",
        message="boom",
        kind=kind,
        node_id=node_id,
        details=details or {},
    )


TRANSIENT = _err(NodeErrorKind.TRANSIENT)
PERMANENT = _err(NodeErrorKind.PERMANENT)


def _version(n_nodes: int) -> ProjectVersion:
    project = Project.create(name="Demo")
    for _ in range(n_nodes):
        project.draft.graph.add_node(NodeInstance.create(node_type="core.log"))
    return project.publish_draft(published_at=T0)


def _execution(
    n_nodes: int = 2,
    *,
    metadata: ExecutionMetadata | None = None,
    retry_policy: RetryPolicy | None = None,
    timeout_policy: TimeoutPolicy | None = None,
    now: datetime | None = None,
) -> Execution:
    return Execution.create(
        project_id="proj_" + "a" * 32,
        project_version=_version(n_nodes),
        metadata=metadata,
        retry_policy=retry_policy,
        timeout_policy=timeout_policy,
        now=now,
    )


def _expect(e: Execution, status: ExecutionStatus) -> None:
    # A function boundary stops mypy narrowing e.status across transitions.
    assert e.status is status


def _ids(e: Execution) -> list[str]:
    return list(e.node_executions)


def _running(
    n_nodes: int = 2,
    *,
    retry_policy: RetryPolicy | None = None,
    timeout_policy: TimeoutPolicy | None = None,
) -> Execution:
    e = _execution(n_nodes, retry_policy=retry_policy, timeout_policy=timeout_policy)
    e.enqueue()
    e.start(now=T0)
    return e


def _run_node(e: Execution, node_id: str, now: datetime = T0) -> None:
    e.mark_node_ready(node_id)
    e.start_node(node_id, now=now)


# ==========================================================================
# 1. Spec: the complete legal transition sets, written out by hand.
# ==========================================================================

EXPECTED_EXECUTION = {
    E.CREATED: {E.QUEUED, E.FAILED, E.CANCELLED},
    E.QUEUED: {E.RUNNING, E.FAILED, E.CANCELLED},
    E.RUNNING: {E.PAUSED, E.WAITING, E.COMPLETED, E.FAILED, E.CANCELLED, E.TIMEOUT},
    E.PAUSED: {E.RUNNING, E.FAILED, E.CANCELLED, E.TIMEOUT},
    E.WAITING: {E.RUNNING, E.FAILED, E.CANCELLED, E.TIMEOUT},
    E.COMPLETED: set(),
    E.FAILED: set(),
    E.CANCELLED: set(),
    E.TIMEOUT: set(),
}

EXPECTED_NODE = {
    N.PENDING: {N.READY, N.SKIPPED, N.CANCELLED},
    N.READY: {N.RUNNING, N.SKIPPED, N.CANCELLED},
    N.RUNNING: {N.READY, N.COMPLETED, N.FAILED, N.CANCELLED},  # READY = retry
    N.COMPLETED: set(),
    N.FAILED: set(),
    N.SKIPPED: set(),
    N.CANCELLED: set(),
}


def test_execution_transition_spec() -> None:
    assert {s: set(t) for s, t in EXECUTION_TRANSITIONS.items()} == EXPECTED_EXECUTION


def test_node_transition_spec() -> None:
    assert {s: set(t) for s, t in NODE_TRANSITIONS.items()} == EXPECTED_NODE


def test_terminal_sets() -> None:
    assert {
        E.COMPLETED,
        E.FAILED,
        E.CANCELLED,
        E.TIMEOUT,
    } == TERMINAL_EXECUTION_STATUSES
    assert {N.COMPLETED, N.FAILED, N.SKIPPED, N.CANCELLED} == TERMINAL_NODE_STATUSES


def _reachable[St](table: dict[St, frozenset[St]], initial: St) -> set[St]:
    reachable, frontier = {initial}, [initial]
    while frontier:
        for t in table[frontier.pop()] - reachable:
            reachable.add(t)
            frontier.append(t)
    return reachable


def test_no_state_is_unreachable() -> None:
    assert _reachable(EXECUTION_TRANSITIONS, E.CREATED) == set(ExecutionStatus)
    assert _reachable(NODE_TRANSITIONS, N.PENDING) == set(NodeExecutionStatus)


def test_created_and_pending_are_never_reentered() -> None:
    assert all(E.CREATED not in t for t in EXECUTION_TRANSITIONS.values())
    assert all(N.PENDING not in t for t in NODE_TRANSITIONS.values())


# ==========================================================================
# 2. Matrix: every state x every action (pure state machine, no nodes).
# ==========================================================================


def _drive_execution(state: ExecutionStatus) -> Execution:
    e = _execution(n_nodes=0)
    path: dict[ExecutionStatus, list[Callable[[], None]]] = {
        E.CREATED: [],
        E.QUEUED: [e.enqueue],
        E.RUNNING: [e.enqueue, e.start],
        E.PAUSED: [e.enqueue, e.start, e.pause],
        E.WAITING: [e.enqueue, e.start, e.wait],
        E.COMPLETED: [e.enqueue, e.start, e.complete],
        E.FAILED: [e.enqueue, e.start, lambda: e.fail(PERMANENT)],
        E.CANCELLED: [e.cancel],
        E.TIMEOUT: [e.enqueue, e.start, e.time_out],
    }
    for step in path[state]:
        step()
    assert e.status is state
    return e


def _execution_action(e: Execution, action: str) -> None:
    if action == "fail":
        e.fail(PERMANENT)
    else:
        getattr(e, action)()


@pytest.mark.parametrize("action", sorted(EXECUTION_ACTIONS))
@pytest.mark.parametrize("state", list(ExecutionStatus))
def test_execution_action_matrix(state: ExecutionStatus, action: str) -> None:
    e = _drive_execution(state)
    sources, target = EXECUTION_ACTIONS[action]
    if state in sources:
        _execution_action(e, action)
        assert e.status is target
    else:
        with pytest.raises(InvalidStateTransitionError) as exc:
            _execution_action(e, action)
        assert e.status is state
        assert exc.value.details["action"] == action
        assert exc.value.details["from"] == str(state)
        assert exc.value.code == "INVALID_STATE_TRANSITION"


@pytest.mark.parametrize(
    ("state", "action"),
    [
        (E.PAUSED, "start"),  # must use resume: start/resume share RUNNING
        (E.WAITING, "start"),
        (E.QUEUED, "resume"),
        (E.CREATED, "start"),  # cannot skip the queue
        (E.CREATED, "complete"),
        (E.QUEUED, "time_out"),  # deadline counts from start
        (E.PAUSED, "complete"),  # must resume before completing
        (E.WAITING, "pause"),
        (E.COMPLETED, "fail"),
        (E.FAILED, "start"),
        (E.CANCELLED, "resume"),
        (E.TIMEOUT, "complete"),
    ],
)
def test_important_illegal_execution_transitions(
    state: ExecutionStatus, action: str
) -> None:
    e = _drive_execution(state)
    with pytest.raises(InvalidStateTransitionError):
        _execution_action(e, action)
    assert e.status is state


def _drive_node(state: NodeExecutionStatus) -> NodeExecution:
    ne = NodeExecution.for_node("node_" + "b" * 32)
    path: dict[NodeExecutionStatus, list[Callable[[], None]]] = {
        N.PENDING: [],
        N.READY: [ne.mark_ready],
        N.RUNNING: [ne.mark_ready, lambda: ne.start(now=T0)],
        N.COMPLETED: [ne.mark_ready, lambda: ne.start(now=T0), ne.complete],
        N.FAILED: [ne.mark_ready, lambda: ne.start(now=T0), lambda: ne.fail(PERMANENT)],
        N.SKIPPED: [ne.skip],
        N.CANCELLED: [ne.cancel],
    }
    for step in path[state]:
        step()
    assert ne.status is state
    return ne


def _node_action(ne: NodeExecution, action: str) -> None:
    match action:
        case "fail":
            ne.fail(PERMANENT)
        case "retry":
            ne.retry(TRANSIENT, retry_at=T0)
        case "start":
            ne.start(now=T0)
        case _:
            getattr(ne, action)()


@pytest.mark.parametrize("action", sorted(NODE_ACTIONS))
@pytest.mark.parametrize("state", list(NodeExecutionStatus))
def test_node_action_matrix(state: NodeExecutionStatus, action: str) -> None:
    ne = _drive_node(state)
    sources, target = NODE_ACTIONS[action]
    if state in sources:
        _node_action(ne, action)
        assert ne.status is target
    else:
        with pytest.raises(InvalidStateTransitionError):
            _node_action(ne, action)
        assert ne.status is state


@pytest.mark.parametrize(
    ("state", "action"),
    [
        (N.PENDING, "start"),  # must become READY first
        (N.RUNNING, "mark_ready"),  # re-readying a running node = use retry
        (N.READY, "complete"),
        (N.READY, "retry"),
        (N.PENDING, "fail"),
        (N.RUNNING, "skip"),  # a started node cannot be skipped
        (N.COMPLETED, "start"),
        (N.FAILED, "retry"),  # failure is final; retry decided while RUNNING
        (N.SKIPPED, "mark_ready"),
        (N.CANCELLED, "start"),
    ],
)
def test_important_illegal_node_transitions(
    state: NodeExecutionStatus, action: str
) -> None:
    ne = _drive_node(state)
    with pytest.raises(InvalidStateTransitionError):
        _node_action(ne, action)
    assert ne.status is state


# ==========================================================================
# 3a. Normal lifecycle
# ==========================================================================


class TestLifecycle:
    def test_normal_lifecycle_with_nodes(self) -> None:
        e = _execution(2, now=T0)
        _expect(e, E.CREATED)
        assert e.created_at == T0
        assert all(ne.status is N.PENDING for ne in e.node_executions.values())
        a, b = _ids(e)

        e.enqueue()
        e.start(now=T0)
        _run_node(e, a, T0)
        e.complete_node(a, now=T0 + S)
        e.mark_node_ready(b)
        e.skip_node(b, now=T0 + S)
        e.complete(now=T0 + 2 * S)

        assert e.status is E.COMPLETED
        assert e.is_terminal
        assert (e.started_at, e.finished_at) == (T0, T0 + 2 * S)
        assert e.node(a).status is N.COMPLETED
        assert e.node(a).attempt == 1
        assert e.node(a).finished_at == T0 + S
        assert e.node(b).status is N.SKIPPED
        assert e.error is None

    def test_node_executions_cover_exactly_the_pinned_graph(self) -> None:
        version = _version(3)
        e = Execution.create(project_id="proj_" + "a" * 32, project_version=version)
        assert set(e.node_executions) == set(version.graph.nodes)
        assert all(k == ne.node_id for k, ne in e.node_executions.items())

    def test_unknown_node_is_rejected(self) -> None:
        e = _running()
        with pytest.raises(DomainValidationError):
            e.start_node("node_" + "f" * 32)

    def test_foreign_node_execution_rejected_at_construction(self) -> None:
        with pytest.raises(DomainValidationError):
            Execution(
                id="exec_" + "a" * 32,
                project_id="proj_" + "a" * 32,
                project_version=_version(1),
                node_executions={
                    "node_" + "f" * 32: NodeExecution.for_node("node_" + "f" * 32)
                },
            )

    def test_pause_and_wait_resume_preserve_started_at(self) -> None:
        e = _running()
        e.pause()
        e.resume()
        e.wait()
        e.resume()
        assert e.status is E.RUNNING
        assert e.started_at == T0

    def test_cannot_complete_with_unfinished_nodes(self) -> None:
        e = _running()
        a, b = _ids(e)
        _run_node(e, a)
        e.complete_node(a)
        with pytest.raises(InvalidStateTransitionError) as exc:
            e.complete()
        assert exc.value.details["node_ids"] == b
        assert e.status is E.RUNNING

    def test_cannot_complete_with_failed_node(self) -> None:
        e = _running(1)
        (a,) = _ids(e)
        _run_node(e, a)
        e.fail_node(a, PERMANENT)
        with pytest.raises(InvalidStateTransitionError):
            e.complete()

    def test_nodes_do_not_start_unless_execution_running(self) -> None:
        for state in (E.CREATED, E.QUEUED, E.PAUSED, E.WAITING):
            e = _execution()
            if state is not E.CREATED:
                e.enqueue()
            if state in (E.PAUSED, E.WAITING):
                e.start()
                e.mark_node_ready(_ids(e)[0])
                e.pause() if state is E.PAUSED else e.wait()
            with pytest.raises(InvalidStateTransitionError):
                e.start_node(_ids(e)[0])

    def test_in_flight_node_may_finish_while_paused(self) -> None:
        e = _running()
        a, _ = _ids(e)
        _run_node(e, a)
        e.pause()
        e.complete_node(a)
        assert e.node(a).status is N.COMPLETED
        assert e.status is E.PAUSED

    def test_metadata(self) -> None:
        e = _execution(
            metadata=ExecutionMetadata(
                trigger_type=TriggerType.API,
                trigger_id="trig_" + "c" * 32,
                correlation_id="req_42",
            )
        )
        assert e.metadata.trigger_type is TriggerType.API
        assert e.metadata.engine_version == __version__
        assert e.metadata.correlation_id == "req_42"

    @pytest.mark.parametrize(
        "make",
        [
            lambda: ExecutionMetadata(trigger_id="not-an-id"),
            lambda: ExecutionMetadata(correlation_id=""),
            lambda: ExecutionMetadata(correlation_id="x" * 129),
            lambda: ExecutionMetadata(correlation_id="evil\ninjected log line"),
            lambda: ExecutionMetadata(engine_version=" "),
            lambda: ExecutionMetadata(trigger_type=TriggerType("carrier-pigeon")),
        ],
    )
    def test_invalid_metadata(self, make: Callable[[], object]) -> None:
        with pytest.raises(ValueError):  # DomainValidationError is a ValueError
            make()

    def test_start_records_resolved_node_versions(self) -> None:
        e = _execution()
        e.enqueue()
        versions = {"core.http": "1.0.0"}
        e.start(now=T0, node_versions=versions)
        versions["core.http"] = "9.9.9"  # the record is a copy
        assert e.metadata.node_versions == {"core.http": "1.0.0"}
        assert e.metadata.engine_version == __version__  # rest untouched

    def test_input_is_a_private_json_object(self) -> None:
        payload = {"lead": {"name": "Ada"}}
        e = Execution.create(
            project_id="proj_" + "a" * 32, project_version=_version(1), input=payload
        )
        payload["lead"]["name"] = "changed"
        assert e.input == {"lead": {"name": "Ada"}}
        assert _execution().input == {}

    @pytest.mark.parametrize(
        "payload",
        [{"x": float("nan")}, {"x": b"bytes"}, {"x": "nul\u0000"}, {"x": object()}],
    )
    def test_input_must_be_storable_json(self, payload: dict[str, object]) -> None:
        with pytest.raises(ValueError, match="JSON object"):
            Execution.create(
                project_id="proj_" + "a" * 32,
                project_version=_version(1),
                input=payload,
            )

    def test_execution_pins_version_not_draft(self) -> None:
        project = Project.create(name="Demo")
        node = NodeInstance.create(node_type="core.log")
        project.draft.graph.add_node(node)
        e = Execution.create(
            project_id=project.id, project_version=project.publish_draft()
        )
        project.draft.graph.remove_node(node.id)
        assert node.id in e.project_version.graph.nodes
        assert node.id in e.node_executions


# ==========================================================================
# 3b. Failure
# ==========================================================================


class TestFailure:
    def test_node_failure_then_execution_failure(self) -> None:
        e = _running()
        a, b = _ids(e)
        _run_node(e, a)
        err = _err(node_id=a)
        e.fail_node(a, err, now=T0 + S)
        e.fail(err, now=T0 + 2 * S)

        assert e.status is E.FAILED
        assert e.error == err
        assert e.node(a).status is N.FAILED
        assert e.node(a).error == err
        # dependents never ran: the terminal cascade cancels them
        assert e.node(b).status is N.CANCELLED
        assert e.node(b).finished_at == T0 + 2 * S

    def test_fail_cancels_running_siblings(self) -> None:
        e = _running()
        a, b = _ids(e)
        _run_node(e, a)
        _run_node(e, b)
        e.fail_node(a, PERMANENT)
        e.fail(PERMANENT)
        assert e.node(b).status is N.CANCELLED

    def test_failure_before_start(self) -> None:
        e = _execution()
        e.enqueue()
        e.fail(ExecutionError("SNAPSHOT_UNAVAILABLE", "x", NodeErrorKind.PERMANENT))
        assert e.status is E.FAILED
        assert e.started_at is None
        assert all(ne.status is N.CANCELLED for ne in e.node_executions.values())

    def test_execution_error_must_reference_known_node(self) -> None:
        e = _running()
        with pytest.raises(DomainValidationError):
            e.fail(_err(node_id="node_" + "f" * 32))
        assert e.status is E.RUNNING

    def test_node_rejects_error_of_other_node(self) -> None:
        e = _running()
        a, b = _ids(e)
        _run_node(e, a)
        with pytest.raises(DomainValidationError):
            e.fail_node(a, _err(node_id=b))
        assert e.node(a).status is N.RUNNING
        assert e.node(a).failures == []

    def test_from_node_error(self) -> None:
        node_id = "node_" + "b" * 32
        err = ExecutionError.from_node_error(
            NodeError(
                "rate limited", kind=NodeErrorKind.TRANSIENT, details={"s": "429"}
            ),
            node_id=node_id,
        )
        assert err.code == "NODE_EXECUTION_ERROR"
        assert err.kind is NodeErrorKind.TRANSIENT
        assert err.retryable
        assert err.node_id == node_id
        assert err.details == {"s": "429"}

    @pytest.mark.parametrize(
        "make",
        [
            lambda: ExecutionError("lower_case", "m", NodeErrorKind.PERMANENT),
            lambda: ExecutionError("", "m", NodeErrorKind.PERMANENT),
            lambda: ExecutionError("X", "  ", NodeErrorKind.PERMANENT),
            lambda: ExecutionError("X", "m", NodeErrorKind.PERMANENT, node_id="bad"),
            lambda: ExecutionError("X", "m", NodeErrorKind("explosion")),
        ],
    )
    def test_invalid_execution_error(self, make: Callable[[], object]) -> None:
        with pytest.raises(ValueError):
            make()

    def test_execution_error_details_are_copied(self) -> None:
        details = {"a": "1"}
        err = _err(details=details)
        details["a"] = "mutated"
        assert err.details == {"a": "1"}


# ==========================================================================
# 3c. Retry
# ==========================================================================


RETRY3 = RetryPolicy(max_attempts=3, initial_backoff=2 * S, backoff_multiplier=2)


class TestRetry:
    def test_transient_error_retry_then_success(self) -> None:
        e = _running(1, retry_policy=RETRY3)
        (a,) = _ids(e)
        _run_node(e, a, T0)

        delay = e.retry_node(a, TRANSIENT, now=T0 + S)
        assert delay == 2 * S
        ne = e.node(a)
        assert ne.status is N.READY
        assert ne.retry_at == T0 + 3 * S
        assert ne.failures == [TRANSIENT]

        e.start_node(a, now=T0 + 3 * S)
        assert ne.attempt == 2
        assert ne.retry_at is None
        e.complete_node(a)
        e.complete()
        assert e.status is E.COMPLETED
        assert ne.error is None  # succeeded; history kept in failures
        assert ne.failures == [TRANSIENT]

    def test_start_before_backoff_elapsed_is_rejected(self) -> None:
        e = _running(1, retry_policy=RETRY3)
        (a,) = _ids(e)
        _run_node(e, a, T0)
        e.retry_node(a, TRANSIENT, now=T0)
        with pytest.raises(InvalidStateTransitionError) as exc:
            e.start_node(a, now=T0 + S)
        assert "retry_at" in exc.value.details
        assert e.node(a).status is N.READY
        assert e.node(a).attempt == 1

    def test_retry_count_exhausted(self) -> None:
        e = _running(1, retry_policy=RETRY3)
        (a,) = _ids(e)
        now = T0
        e.mark_node_ready(a)
        for _ in range(2):
            e.start_node(a, now=now)
            now += e.retry_node(a, TRANSIENT, now=now)
        e.start_node(a, now=now)
        assert e.node(a).attempt == 3
        with pytest.raises(InvalidStateTransitionError) as exc:
            e.retry_node(a, TRANSIENT, now=now)
        assert exc.value.details["attempt"] == "3"
        assert e.node(a).status is N.RUNNING  # rejection changed nothing
        # final failure is recorded explicitly by the runtime
        e.fail_node(a, TRANSIENT)
        assert e.node(a).status is N.FAILED
        assert len(e.node(a).failures) == 3

    @pytest.mark.parametrize(
        "kind",
        [
            NodeErrorKind.PERMANENT,
            NodeErrorKind.VALIDATION,
            NodeErrorKind.CONFIGURATION,
            NodeErrorKind.CANCELLATION,
        ],
    )
    def test_non_retryable_kinds_are_never_retried(self, kind: NodeErrorKind) -> None:
        e = _running(1, retry_policy=RETRY3)
        (a,) = _ids(e)
        _run_node(e, a)
        with pytest.raises(InvalidStateTransitionError):
            e.retry_node(a, _err(kind))
        assert e.node(a).status is N.RUNNING

    def test_default_policy_never_retries(self) -> None:
        e = _running(1)
        (a,) = _ids(e)
        _run_node(e, a)
        with pytest.raises(InvalidStateTransitionError):
            e.retry_node(a, TRANSIENT)

    def test_retry_blocked_after_cancellation_request(self) -> None:
        e = _running(1, retry_policy=RETRY3)
        (a,) = _ids(e)
        _run_node(e, a)
        e.request_cancellation()
        with pytest.raises(InvalidStateTransitionError):
            e.retry_node(a, TRANSIENT)
        assert e.node(a).status is N.RUNNING

    def test_retry_of_non_running_node_rejected(self) -> None:
        e = _running(1, retry_policy=RETRY3)
        (a,) = _ids(e)
        e.mark_node_ready(a)
        with pytest.raises(InvalidStateTransitionError):
            e.retry_node(a, TRANSIENT)

    def test_exponential_backoff_with_max_delay(self) -> None:
        p = RetryPolicy(
            max_attempts=10, initial_backoff=S, backoff_multiplier=2, max_backoff=10 * S
        )
        assert [p.backoff(n) for n in range(1, 7)] == [
            S, 2 * S, 4 * S, 8 * S, 10 * S, 10 * S,
        ]  # fmt: skip

    def test_backoff_does_not_overflow(self) -> None:
        assert RetryPolicy(max_backoff=60 * S).backoff(10**9) == 60 * S

    def test_backoff_attempt_must_be_positive(self) -> None:
        with pytest.raises(DomainValidationError):
            RetryPolicy().backoff(0)

    @pytest.mark.parametrize(
        "make",
        [
            lambda: RetryPolicy(max_attempts=0),
            lambda: RetryPolicy(initial_backoff=-S),
            lambda: RetryPolicy(backoff_multiplier=0.5),
            lambda: RetryPolicy(initial_backoff=10 * S, max_backoff=S),
            lambda: RetryPolicy(
                retryable_kinds=frozenset({NodeErrorKind.CANCELLATION})
            ),
        ],
    )
    def test_invalid_retry_policy(self, make: Callable[[], object]) -> None:
        with pytest.raises(DomainValidationError):
            make()


# ==========================================================================
# 3d. Timeout
# ==========================================================================


class TestTimeout:
    def test_execution_deadline(self) -> None:
        e = _running(timeout_policy=TimeoutPolicy(execution_timeout=10 * S))
        assert not e.is_timed_out(T0 + 9 * S)
        assert e.is_timed_out(T0 + 10 * S)

    def test_not_started_or_unlimited_never_times_out(self) -> None:
        e = _execution(timeout_policy=TimeoutPolicy(execution_timeout=S))
        assert not e.is_timed_out(T0 + 1000 * S)
        assert not _running().is_timed_out(T0 + timedelta(days=365))

    def test_time_out_is_explicit_state_with_error_and_cascade(self) -> None:
        e = _running()
        a, b = _ids(e)
        _run_node(e, a)
        e.time_out(now=T0 + 5 * S)
        assert e.status is E.TIMEOUT
        assert e.is_terminal
        assert e.error is not None
        assert e.error.code == "EXECUTION_TIMEOUT"
        assert e.error.kind is NodeErrorKind.TIMEOUT
        assert e.node(a).status is N.CANCELLED
        assert e.node(b).status is N.CANCELLED
        assert e.finished_at == T0 + 5 * S

    @pytest.mark.parametrize("pause", ["pause", "wait"])
    def test_paused_or_waiting_execution_can_time_out(self, pause: str) -> None:
        e = _running()
        getattr(e, pause)()
        e.time_out()
        assert e.status is E.TIMEOUT

    def test_node_timeout_is_retryable_error(self) -> None:
        policy = TimeoutPolicy(node_timeout=30 * S)
        e = _running(1, retry_policy=RETRY3, timeout_policy=policy)
        (a,) = _ids(e)
        _run_node(e, a, T0)
        assert policy.node_deadline(e.node(a).started_at) == T0 + 30 * S

        err = ExecutionError.node_timeout(node_id=a, timeout=30 * S)
        assert err.retryable
        e.retry_node(a, err, now=T0 + 30 * S)
        assert e.node(a).failures[-1].code == "NODE_TIMEOUT"

    def test_node_timeout_final_failure(self) -> None:
        e = _running(1)
        (a,) = _ids(e)
        _run_node(e, a)
        e.fail_node(a, ExecutionError.node_timeout(node_id=a, timeout=S))
        error = e.node(a).error
        assert error is not None
        assert error.kind is NodeErrorKind.TIMEOUT

    def test_execution_timeout_during_retry_backoff(self) -> None:
        e = _running(1, retry_policy=RETRY3)
        (a,) = _ids(e)
        _run_node(e, a)
        e.retry_node(a, TRANSIENT, now=T0)
        e.time_out(now=T0 + S)
        assert e.node(a).status is N.CANCELLED
        with pytest.raises(InvalidStateTransitionError):
            e.start_node(a, now=T0 + 10 * S)

    def test_node_deadline_per_attempt(self) -> None:
        policy = TimeoutPolicy(node_timeout=30 * S)
        assert policy.node_deadline(None) is None
        assert TimeoutPolicy().node_deadline(T0) is None

    @pytest.mark.parametrize("limit", [timedelta(0), -S])
    def test_invalid_timeout_policy(self, limit: timedelta) -> None:
        with pytest.raises(DomainValidationError):
            TimeoutPolicy(execution_timeout=limit)
        with pytest.raises(DomainValidationError):
            TimeoutPolicy(node_timeout=limit)


# ==========================================================================
# 3e. Cancellation
# ==========================================================================


class TestCancellation:
    @pytest.mark.parametrize("state", [E.CREATED, E.QUEUED])
    def test_cancel_before_running(self, state: ExecutionStatus) -> None:
        e = _execution()
        if state is E.QUEUED:
            e.enqueue()
        e.cancel(reason="user", now=T0)
        assert e.status is E.CANCELLED
        assert e.cancellation == CancellationState(T0, "user")
        assert all(ne.status is N.CANCELLED for ne in e.node_executions.values())

    def test_cooperative_cancel_while_running(self) -> None:
        e = _running()
        a, b = _ids(e)
        _run_node(e, a)
        e.request_cancellation(reason="user", now=T0 + S)
        assert e.is_cancellation_requested
        _expect(e, E.RUNNING)  # still running until nodes cooperate

        e.cancel(now=T0 + 2 * S)
        assert e.status is E.CANCELLED
        assert e.cancellation == CancellationState(T0 + S, "user")  # first request
        assert e.node(a).status is N.CANCELLED
        assert e.node(b).status is N.CANCELLED
        assert e.error is None

    def test_request_is_idempotent(self) -> None:
        e = _running()
        e.request_cancellation(reason="first", now=T0)
        e.request_cancellation(reason="second", now=T0 + S)
        assert e.cancellation == CancellationState(T0, "first")

    @pytest.mark.parametrize(
        "action",
        ["pause", "wait", "complete", "time_out", "fail"],
    )
    def test_after_request_only_cancel_is_legal(self, action: str) -> None:
        e = _running(0)
        e.request_cancellation()
        with pytest.raises(InvalidStateTransitionError):
            _execution_action(e, action)
        assert e.status is E.RUNNING

    def test_paused_then_cancel_request_cannot_resume(self) -> None:
        e = _running()
        e.pause()
        e.request_cancellation()
        with pytest.raises(InvalidStateTransitionError):
            e.resume()

    def test_cancelled_execution_never_reports_completed(self) -> None:
        e = _running(1)
        (a,) = _ids(e)
        _run_node(e, a)
        e.request_cancellation()
        e.complete_node(a)  # in-flight work finished before noticing: truthful
        with pytest.raises(InvalidStateTransitionError):
            e.complete()
        e.cancel()
        assert e.status is E.CANCELLED
        assert e.node(a).status is N.COMPLETED  # its side effects happened

    @pytest.mark.parametrize("op", ["mark_node_ready", "start_node", "skip_node"])
    def test_no_new_node_work_after_request(self, op: str) -> None:
        e = _running(1)
        (a,) = _ids(e)
        if op == "start_node":
            e.mark_node_ready(a)
        e.request_cancellation()
        with pytest.raises(InvalidStateTransitionError):
            getattr(e, op)(a)

    def test_cancel_during_node_execution(self) -> None:
        e = _running(1)
        (a,) = _ids(e)
        _run_node(e, a)
        e.cancel(now=T0 + S)
        assert e.node(a).status is N.CANCELLED
        assert e.node(a).finished_at == T0 + S
        with pytest.raises(InvalidStateTransitionError):
            e.complete_node(a)

    def test_cancel_during_retry_backoff(self) -> None:
        e = _running(1, retry_policy=RETRY3)
        (a,) = _ids(e)
        _run_node(e, a)
        e.retry_node(a, TRANSIENT, now=T0)
        e.cancel()
        assert e.node(a).status is N.CANCELLED
        with pytest.raises(InvalidStateTransitionError):
            e.start_node(a, now=T0 + timedelta(hours=1))

    def test_node_cancellation_error_is_final(self) -> None:
        e = _running(1, retry_policy=RETRY3)
        (a,) = _ids(e)
        _run_node(e, a)
        err = _err(NodeErrorKind.CANCELLATION)
        with pytest.raises(InvalidStateTransitionError):
            e.retry_node(a, err)


# ==========================================================================
# 3f. Terminal state behaviour
# ==========================================================================


@pytest.mark.parametrize("state", sorted(TERMINAL_EXECUTION_STATUSES))
class TestTerminal:
    def test_every_execution_action_rejected(self, state: ExecutionStatus) -> None:
        for action in EXECUTION_ACTIONS:
            e = _drive_execution(state)
            finished_at = e.finished_at
            with pytest.raises(InvalidStateTransitionError):
                _execution_action(e, action)
            assert (e.status, e.finished_at) == (state, finished_at)

    def test_cancellation_request_rejected(self, state: ExecutionStatus) -> None:
        e = _drive_execution(state)
        with pytest.raises(InvalidStateTransitionError):
            e.request_cancellation()

    def test_node_operations_rejected(self, state: ExecutionStatus) -> None:
        e = _execution(1, retry_policy=RETRY3)
        (a,) = _ids(e)
        e.enqueue()
        e.start()
        _run_node(e, a)
        if state is E.COMPLETED:
            e.complete_node(a)
        {
            E.COMPLETED: e.complete,
            E.FAILED: lambda: e.fail(PERMANENT),
            E.CANCELLED: e.cancel,
            E.TIMEOUT: e.time_out,
        }[state]()
        for op in (
            lambda: e.mark_node_ready(a),
            lambda: e.start_node(a),
            lambda: e.skip_node(a),
            lambda: e.complete_node(a),
            lambda: e.fail_node(a, PERMANENT),
            lambda: e.retry_node(a, TRANSIENT),
        ):
            with pytest.raises(InvalidStateTransitionError):
                op()
        assert e.node(a).is_terminal


# ==========================================================================
# 4. Stateful fuzz: random action sequences must never break invariants.
# ==========================================================================


def _snapshot(e: Execution) -> object:
    return (
        e.status,
        e.error,
        e.cancellation,
        e.started_at,
        e.finished_at,
        tuple(
            (
                n.status,
                n.attempt,
                tuple(n.failures),
                n.retry_at,
                n.started_at,
                n.finished_at,
            )
            for n in e.node_executions.values()
        ),
    )


_ERRORS = st.sampled_from([TRANSIENT, PERMANENT, _err(NodeErrorKind.TIMEOUT)])


class ExecutionMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.now = T0
        self.e = _execution(
            3, retry_policy=RetryPolicy(max_attempts=3, initial_backoff=S)
        )
        self.ids = _ids(self.e)
        self.saw_cancel_request = False

    def _try(self, op: Callable[[], object]) -> None:
        before = _snapshot(self.e)
        try:
            op()
        except InvalidStateTransitionError:
            assert _snapshot(self.e) == before, "rejected transition mutated state"

    @rule(seconds=st.integers(0, 5))
    def tick(self, seconds: int) -> None:
        self.now += seconds * S

    @rule(action=st.sampled_from(sorted(EXECUTION_ACTIONS)), error=_ERRORS)
    def execution_action(self, action: str, error: ExecutionError) -> None:
        e, now = self.e, self.now
        ops: dict[str, Callable[[], None]] = {
            "enqueue": e.enqueue,
            "start": lambda: e.start(now=now),
            "pause": e.pause,
            "wait": e.wait,
            "resume": e.resume,
            "complete": lambda: e.complete(now=now),
            "fail": lambda: e.fail(error, now=now),
            "cancel": lambda: e.cancel(now=now),
            "time_out": lambda: e.time_out(now=now),
        }
        self._try(ops[action])

    @rule()
    def request_cancellation(self) -> None:
        self._try(lambda: self.e.request_cancellation(now=self.now))
        self.saw_cancel_request |= self.e.is_cancellation_requested

    @rule(
        idx=st.integers(0, 2),
        op=st.sampled_from(["ready", "start", "skip", "complete", "fail", "retry"]),
        error=_ERRORS,
    )
    def node_action(self, idx: int, op: str, error: ExecutionError) -> None:
        e, nid, now = self.e, self.ids[idx], self.now
        ops: dict[str, Callable[[], object]] = {
            "ready": lambda: e.mark_node_ready(nid),
            "start": lambda: e.start_node(nid, now=now),
            "skip": lambda: e.skip_node(nid, now=now),
            "complete": lambda: e.complete_node(nid, now=now),
            "fail": lambda: e.fail_node(nid, error, now=now),
            "retry": lambda: e.retry_node(nid, error, now=now),
        }
        self._try(ops[op])

    @invariant()
    def terminal_execution_has_only_terminal_nodes(self) -> None:
        if self.e.is_terminal:
            assert all(n.is_terminal for n in self.e.node_executions.values())
            assert self.e.finished_at is not None

    @invariant()
    def completed_means_every_node_completed_or_skipped(self) -> None:
        if self.e.status is E.COMPLETED:
            assert all(
                n.status in (N.COMPLETED, N.SKIPPED)
                for n in self.e.node_executions.values()
            )

    @invariant()
    def cancellation_request_is_sticky_and_never_completes(self) -> None:
        if self.saw_cancel_request:
            assert self.e.is_cancellation_requested
            assert self.e.status not in (E.COMPLETED, E.FAILED, E.TIMEOUT)
        if self.e.status is E.CANCELLED:
            assert self.e.cancellation is not None

    @invariant()
    def running_nodes_only_in_started_executions(self) -> None:
        if any(n.status is N.RUNNING for n in self.e.node_executions.values()):
            assert self.e.status in (E.RUNNING, E.PAUSED, E.WAITING)

    @invariant()
    def attempts_bounded_by_policy(self) -> None:
        for n in self.e.node_executions.values():
            assert n.attempt <= self.e.retry_policy.max_attempts
            assert len(n.failures) <= n.attempt

    @invariant()
    def failure_states_carry_errors(self) -> None:
        if self.e.status in (E.FAILED, E.TIMEOUT):
            assert self.e.error is not None
        for n in self.e.node_executions.values():
            assert (n.error is not None) == (n.status is N.FAILED)


ExecutionMachine.TestCase.settings = settings(
    max_examples=300, stateful_step_count=40, deadline=None
)
test_execution_state_machine_fuzz = ExecutionMachine.TestCase


def test_graph_without_nodes_can_complete() -> None:
    # Degenerate but legal snapshot; the runtime rejects empty graphs via
    # validation, not the state machine.
    e = Execution.create(
        project_id="proj_" + "a" * 32,
        project_version=ProjectVersion(
            id="pver_" + "a" * 32, version=1, schema_version=1, graph=Graph()
        ),
    )
    e.enqueue()
    e.start()
    e.complete()
    assert e.status is E.COMPLETED


# ==========================================================================
# 5. Regressions found by deliberately attacking the model (BREAK step).
# ==========================================================================


class TestBreakRegressions:
    def test_start_cannot_masquerade_as_resume(self) -> None:
        # Phase 02 keyed transitions by target, so start() from PAUSED passed
        # because resume() also targets RUNNING.
        e = _running()
        e.pause()
        with pytest.raises(InvalidStateTransitionError):
            e.start()

    def test_node_awaiting_retry_cannot_be_skipped(self) -> None:
        e = _running(1, retry_policy=RETRY3)
        (a,) = _ids(e)
        _run_node(e, a)
        e.retry_node(a, TRANSIENT, now=T0)
        with pytest.raises(InvalidStateTransitionError):
            e.skip_node(a)
        assert e.node(a).status is N.READY

    @pytest.mark.parametrize(
        "status",
        # COMPLETED with a PENDING node; the others without error/cancellation
        [E.COMPLETED, E.FAILED, E.TIMEOUT, E.CANCELLED],
    )
    def test_constructor_rejects_unreachable_states(
        self, status: ExecutionStatus
    ) -> None:
        version = _version(1)
        (node_id,) = version.graph.nodes
        ne = NodeExecution.for_node(node_id)
        if status is not E.COMPLETED:
            ne.cancel()
        with pytest.raises(DomainValidationError):
            Execution(
                id="exec_" + "a" * 32,
                project_id="proj_" + "a" * 32,
                project_version=version,
                status=status,
                node_executions={node_id: ne},
            )

    def test_constructor_rejects_failed_node_without_error(self) -> None:
        with pytest.raises(DomainValidationError):
            NodeExecution(
                id="nexec_" + "a" * 32, node_id="node_" + "a" * 32, status=N.FAILED
            )

    def test_constructor_rejects_more_failures_than_attempts(self) -> None:
        with pytest.raises(DomainValidationError):
            NodeExecution(
                id="nexec_" + "a" * 32,
                node_id="node_" + "a" * 32,
                attempt=0,
                failures=[PERMANENT],
            )

    def test_unknown_status_string_rejected(self) -> None:
        with pytest.raises(ValueError):
            Execution(
                id="exec_" + "a" * 32,
                project_id="proj_" + "a" * 32,
                project_version=_version(0),
                status="exploded",  # type: ignore[arg-type]  # the attack itself
            )


class TestPhase08Additions:
    """Outputs, revision and the extra rehydration invariant (Phase 08)."""

    def test_completed_node_records_a_private_copy_of_its_outputs(self) -> None:
        e = _running(1)
        (a,) = _ids(e)
        e.mark_node_ready(a)
        e.start_node(a, now=T0)
        produced = {"out": "x"}
        e.complete_node(a, outputs=produced, now=T0)
        produced["out"] = "mutated later"
        assert e.node(a).outputs == {"out": "x"}

    def test_only_completed_nodes_carry_outputs(self) -> None:
        with pytest.raises(DomainValidationError):
            NodeExecution(
                id="nexec_" + "a" * 32,
                node_id="node_" + "a" * 32,
                status=N.CANCELLED,
                outputs={"out": 1},
            )

    @pytest.mark.parametrize("status", [E.CREATED, E.QUEUED])
    def test_unstarted_execution_rejects_progressed_nodes(
        self, status: ExecutionStatus
    ) -> None:
        # Closes Phase 07 L6 for rehydration: e.g. a torn read produced a
        # QUEUED execution whose nodes were already READY.
        version = _version(1)
        (node_id,) = version.graph.nodes
        ne = NodeExecution.for_node(node_id)
        ne.mark_ready()
        with pytest.raises(DomainValidationError):
            Execution(
                id="exec_" + "a" * 32,
                project_id="proj_" + "a" * 32,
                project_version=version,
                status=status,
                node_executions={node_id: ne},
            )

    def test_negative_revision_rejected(self) -> None:
        with pytest.raises(DomainValidationError):
            Execution(
                id="exec_" + "a" * 32,
                project_id="proj_" + "a" * 32,
                project_version=_version(0),
                revision=-1,
            )
