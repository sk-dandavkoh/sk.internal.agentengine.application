"""Execution and NodeExecution: WHAT happened / is happening.

An Execution always pins an immutable ProjectVersion snapshot — never a draft
(vision §5.5). Status is changed only through explicit transition methods that
enforce the state machine (architecture §3.6); terminal states have no exits.

Full runtime policies (retry, timeout enforcement) are Phase 07+; the state
machines themselves belong here because they are domain invariants.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum, auto

from akasha.domain.primitives import Entity, new_id, utcnow, validate_id
from akasha.domain.project import ProjectVersion
from akasha.domain.trigger import TriggerType
from akasha.errors import InvalidStateTransitionError


class ExecutionStatus(StrEnum):
    CREATED = auto()
    QUEUED = auto()
    RUNNING = auto()
    PAUSED = auto()
    WAITING = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()
    TIMEOUT = auto()


class NodeExecutionStatus(StrEnum):
    PENDING = auto()
    READY = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    SKIPPED = auto()
    CANCELLED = auto()


# Legal transitions; terminal states map to nothing.
_EXECUTION_TRANSITIONS: dict[ExecutionStatus, frozenset[ExecutionStatus]] = {
    ExecutionStatus.CREATED: frozenset(
        {ExecutionStatus.QUEUED, ExecutionStatus.CANCELLED}
    ),
    ExecutionStatus.QUEUED: frozenset(
        {ExecutionStatus.RUNNING, ExecutionStatus.CANCELLED}
    ),
    ExecutionStatus.RUNNING: frozenset(
        {
            ExecutionStatus.PAUSED,
            ExecutionStatus.WAITING,
            ExecutionStatus.COMPLETED,
            ExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.TIMEOUT,
        }
    ),
    ExecutionStatus.PAUSED: frozenset(
        {ExecutionStatus.RUNNING, ExecutionStatus.CANCELLED}
    ),
    ExecutionStatus.WAITING: frozenset(
        {ExecutionStatus.RUNNING, ExecutionStatus.CANCELLED}
    ),
    ExecutionStatus.COMPLETED: frozenset(),
    ExecutionStatus.FAILED: frozenset(),
    ExecutionStatus.CANCELLED: frozenset(),
    ExecutionStatus.TIMEOUT: frozenset(),
}

_NODE_TRANSITIONS: dict[NodeExecutionStatus, frozenset[NodeExecutionStatus]] = {
    NodeExecutionStatus.PENDING: frozenset(
        {
            NodeExecutionStatus.READY,
            NodeExecutionStatus.SKIPPED,
            NodeExecutionStatus.CANCELLED,
        }
    ),
    NodeExecutionStatus.READY: frozenset(
        {
            NodeExecutionStatus.RUNNING,
            NodeExecutionStatus.CANCELLED,
            NodeExecutionStatus.SKIPPED,
        }
    ),
    NodeExecutionStatus.RUNNING: frozenset(
        {
            NodeExecutionStatus.COMPLETED,
            NodeExecutionStatus.FAILED,
            NodeExecutionStatus.CANCELLED,
        }
    ),
    NodeExecutionStatus.COMPLETED: frozenset(),
    NodeExecutionStatus.FAILED: frozenset(),
    NodeExecutionStatus.SKIPPED: frozenset(),
    NodeExecutionStatus.CANCELLED: frozenset(),
}


def _check_transition[Status: StrEnum](
    current: Status, target: Status, table: dict[Status, frozenset[Status]]
) -> None:
    if target not in table[current]:
        raise InvalidStateTransitionError(
            f"illegal transition: {current} -> {target}",
            details={"from": str(current), "to": str(target)},
        )


@dataclass(eq=False)
class NodeExecution(Entity):
    """State of one node instance within one execution."""

    node_id: str  # id of the NodeInstance in the pinned ProjectVersion
    status: NodeExecutionStatus = NodeExecutionStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        self.id = validate_id(self.id)
        validate_id(self.node_id)
        self.status = NodeExecutionStatus(self.status)

    @classmethod
    def for_node(cls, node_id: str) -> "NodeExecution":
        return cls(id=new_id("nexec"), node_id=node_id)

    def _transition(self, to: NodeExecutionStatus, now: datetime | None = None) -> None:
        _check_transition(self.status, to, _NODE_TRANSITIONS)
        self.status = to
        if to is NodeExecutionStatus.RUNNING:
            self.started_at = now or utcnow()
        if to in (
            NodeExecutionStatus.COMPLETED,
            NodeExecutionStatus.FAILED,
            NodeExecutionStatus.CANCELLED,
            NodeExecutionStatus.SKIPPED,
        ):
            self.finished_at = now or utcnow()

    def mark_ready(self) -> None:
        self._transition(NodeExecutionStatus.READY)

    def start(self, *, now: datetime | None = None) -> None:
        self._transition(NodeExecutionStatus.RUNNING, now)

    def complete(self, *, now: datetime | None = None) -> None:
        self._transition(NodeExecutionStatus.COMPLETED, now)

    def fail(self, error: str, *, now: datetime | None = None) -> None:
        self._transition(NodeExecutionStatus.FAILED, now)
        self.error = error

    def skip(self) -> None:
        self._transition(NodeExecutionStatus.SKIPPED)

    def cancel(self, *, now: datetime | None = None) -> None:
        self._transition(NodeExecutionStatus.CANCELLED, now)


@dataclass(eq=False)
class Execution(Entity):
    """One run of one immutable ProjectVersion snapshot."""

    project_id: str
    project_version: ProjectVersion
    trigger_type: TriggerType = TriggerType.MANUAL
    status: ExecutionStatus = ExecutionStatus.CREATED
    node_executions: list[NodeExecution] = field(default_factory=list)
    error: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        self.id = validate_id(self.id)
        validate_id(self.project_id)
        self.status = ExecutionStatus(self.status)
        self.trigger_type = TriggerType(self.trigger_type)

    @classmethod
    def create(
        cls,
        project_id: str,
        project_version: ProjectVersion,
        *,
        trigger_type: TriggerType = TriggerType.MANUAL,
    ) -> "Execution":
        return cls(
            id=new_id("exec"),
            project_id=project_id,
            project_version=project_version,
            trigger_type=trigger_type,
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            ExecutionStatus.COMPLETED,
            ExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.TIMEOUT,
        )

    def _transition(self, to: ExecutionStatus, now: datetime | None = None) -> None:
        _check_transition(self.status, to, _EXECUTION_TRANSITIONS)
        self.status = to
        if to is ExecutionStatus.RUNNING and self.started_at is None:
            self.started_at = now or utcnow()
        if to in (
            ExecutionStatus.COMPLETED,
            ExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.TIMEOUT,
        ):
            self.finished_at = now or utcnow()

    def enqueue(self) -> None:
        self._transition(ExecutionStatus.QUEUED)

    def start(self, *, now: datetime | None = None) -> None:
        self._transition(ExecutionStatus.RUNNING, now)

    def pause(self) -> None:
        self._transition(ExecutionStatus.PAUSED)

    def wait(self) -> None:
        self._transition(ExecutionStatus.WAITING)

    def resume(self) -> None:
        self._transition(ExecutionStatus.RUNNING)

    def complete(self, *, now: datetime | None = None) -> None:
        self._transition(ExecutionStatus.COMPLETED, now)

    def fail(self, error: str, *, now: datetime | None = None) -> None:
        self._transition(ExecutionStatus.FAILED, now)
        self.error = error

    def cancel(self, *, now: datetime | None = None) -> None:
        self._transition(ExecutionStatus.CANCELLED, now)

    def time_out(self, *, now: datetime | None = None) -> None:
        self._transition(ExecutionStatus.TIMEOUT, now)
