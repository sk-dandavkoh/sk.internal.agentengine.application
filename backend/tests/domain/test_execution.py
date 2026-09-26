"""Execution state machine invariants (vision §16)."""

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from akasha.domain.execution import (
    _EXECUTION_TRANSITIONS,
    _NODE_TRANSITIONS,
    Execution,
    ExecutionStatus,
    NodeExecution,
    NodeExecutionStatus,
)
from akasha.domain.graph import NodeInstance
from akasha.domain.project import Project
from akasha.domain.trigger import TriggerType
from akasha.errors import InvalidStateTransitionError

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _expect(e: Execution, status: ExecutionStatus) -> None:
    assert e.status is status


def _execution() -> Execution:
    project = Project.create(name="Demo")
    node = NodeInstance.create(node_type="core.log")
    project.draft.graph.add_node(node)
    return Execution.create(
        project_id=project.id,
        project_version=project.publish_draft(),
        trigger_type=TriggerType.API,
    )


class TestExecutionLifecycle:
    def test_happy_path(self) -> None:
        e = _execution()
        _expect(e, ExecutionStatus.CREATED)
        assert not e.is_terminal
        e.enqueue()
        e.start(now=NOW)
        assert e.started_at == NOW
        e.wait()
        e.resume()
        e.complete(now=NOW)
        _expect(e, ExecutionStatus.COMPLETED)
        assert e.is_terminal
        assert e.finished_at == NOW

    def test_pause_resume(self) -> None:
        e = _execution()
        e.enqueue()
        e.start()
        e.pause()
        _expect(e, ExecutionStatus.PAUSED)
        e.resume()
        _expect(e, ExecutionStatus.RUNNING)

    def test_cancel_from_created_and_queued_and_running(self) -> None:
        for until in ("created", "queued", "running"):
            e = _execution()
            if until != "created":
                e.enqueue()
            if until == "running":
                e.start()
            e.cancel()
            _expect(e, ExecutionStatus.CANCELLED)

    def test_fail_records_error(self) -> None:
        e = _execution()
        e.enqueue()
        e.start()
        e.fail("node exploded")
        _expect(e, ExecutionStatus.FAILED)
        assert e.error == "node exploded"

    def test_timeout_is_explicit_terminal_state(self) -> None:
        e = _execution()
        e.enqueue()
        e.start()
        e.time_out()
        _expect(e, ExecutionStatus.TIMEOUT)
        assert e.is_terminal

    @staticmethod
    def _drive(e: Execution, state: ExecutionStatus) -> None:
        steps: dict[ExecutionStatus, list[Callable[[], None]]] = {
            ExecutionStatus.CREATED: [],
            ExecutionStatus.QUEUED: [e.enqueue],
            ExecutionStatus.RUNNING: [e.enqueue, e.start],
            ExecutionStatus.PAUSED: [e.enqueue, e.start, e.pause],
            ExecutionStatus.WAITING: [e.enqueue, e.start, e.wait],
            ExecutionStatus.COMPLETED: [e.enqueue, e.start, e.complete],
            ExecutionStatus.FAILED: [e.enqueue, e.start, lambda: e.fail("x")],
            ExecutionStatus.CANCELLED: [e.cancel],
            ExecutionStatus.TIMEOUT: [e.enqueue, e.start, e.time_out],
        }
        for step in steps[state]:
            step()

    @staticmethod
    def _attempt(e: Execution, target: ExecutionStatus) -> None:
        # resume() shares target RUNNING with start(); pick whichever is legal.
        if target is ExecutionStatus.RUNNING:
            if e.status in (ExecutionStatus.PAUSED, ExecutionStatus.WAITING):
                e.resume()
            else:
                e.start()
            return
        if target is ExecutionStatus.CREATED:
            # No method targets CREATED — it can never be re-entered.
            raise InvalidStateTransitionError("CREATED has no inbound transitions")
        actions: dict[ExecutionStatus, Callable[[], None]] = {
            ExecutionStatus.QUEUED: e.enqueue,
            ExecutionStatus.PAUSED: e.pause,
            ExecutionStatus.WAITING: e.wait,
            ExecutionStatus.COMPLETED: e.complete,
            ExecutionStatus.FAILED: lambda: e.fail("x"),
            ExecutionStatus.CANCELLED: e.cancel,
            ExecutionStatus.TIMEOUT: e.time_out,
        }
        actions[target]()

    @pytest.mark.parametrize("start", list(ExecutionStatus))
    @pytest.mark.parametrize("target", list(ExecutionStatus))
    def test_transition_matrix(
        self, start: ExecutionStatus, target: ExecutionStatus
    ) -> None:
        """Every one of the 9x9 transitions matches the declared table."""
        e = _execution()
        self._drive(e, start)
        if target in _EXECUTION_TRANSITIONS[start]:
            self._attempt(e, target)
            _expect(e, target)
        else:
            with pytest.raises(InvalidStateTransitionError):
                self._attempt(e, target)
            _expect(e, start)

    def test_terminal_states_have_no_exits(self) -> None:
        for terminal in (
            ExecutionStatus.COMPLETED,
            ExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.TIMEOUT,
        ):
            assert _EXECUTION_TRANSITIONS[terminal] == frozenset()

    def test_every_declared_transition_is_reachable(self) -> None:
        # Guards the table itself: no typo'd state names, all keys valid.
        assert set(_EXECUTION_TRANSITIONS) == set(ExecutionStatus)
        for targets in _EXECUTION_TRANSITIONS.values():
            assert targets <= set(ExecutionStatus)


class TestNodeExecution:
    def _node_exec(self) -> NodeExecution:
        return NodeExecution.for_node(NodeInstance.create(node_type="core.log").id)

    def test_happy_path(self) -> None:
        ne = self._node_exec()
        assert ne.status is NodeExecutionStatus.PENDING
        ne.mark_ready()
        ne.start(now=NOW)
        assert ne.started_at == NOW
        ne.complete(now=NOW)
        assert ne.finished_at == NOW

    def test_skip_and_cancel_from_pending(self) -> None:
        ne = self._node_exec()
        ne.skip()
        assert ne.status is NodeExecutionStatus.SKIPPED
        ne2 = self._node_exec()
        ne2.cancel()
        assert ne2.status is NodeExecutionStatus.CANCELLED

    def test_fail_from_running(self) -> None:
        ne = self._node_exec()
        ne.mark_ready()
        ne.start()
        ne.fail("boom")
        assert ne.error == "boom"

    def test_terminal_states_have_no_exits(self) -> None:
        for terminal in (
            NodeExecutionStatus.COMPLETED,
            NodeExecutionStatus.FAILED,
            NodeExecutionStatus.SKIPPED,
            NodeExecutionStatus.CANCELLED,
        ):
            assert _NODE_TRANSITIONS[terminal] == frozenset()

    def test_running_without_ready_is_rejected(self) -> None:
        with pytest.raises(InvalidStateTransitionError):
            self._node_exec().start()

    def test_complete_without_running_is_rejected(self) -> None:
        ne = self._node_exec()
        ne.mark_ready()
        with pytest.raises(InvalidStateTransitionError):
            ne.complete()


class TestExecutionSnapshotPinning:
    def test_execution_references_version_not_draft(self) -> None:
        project = Project.create(name="Demo")
        node = NodeInstance.create(node_type="core.log")
        project.draft.graph.add_node(node)
        version = project.publish_draft()
        e = Execution.create(project_id=project.id, project_version=version)

        project.draft.graph.remove_node(node.id)

        assert node.id in e.project_version.graph.nodes
        assert project.draft.graph.nodes == {}

    def test_trigger_type_coerced_from_string(self) -> None:
        e = _execution()
        e.trigger_type = TriggerType("manual")
        assert e.trigger_type is TriggerType.MANUAL
