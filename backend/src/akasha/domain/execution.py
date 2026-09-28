"""Execution and NodeExecution: WHAT happened / is happening.

An Execution always pins an immutable ProjectVersion snapshot — never a draft
(vision §5.5). Status changes only through named transition methods
(architecture §3.6); there is no other way to move a status.

State machines are data: every named action maps to ``(legal sources,
target)``. Keying by *action* rather than by target matters because two
actions can share a target (``start``/``resume`` → RUNNING, ``mark_ready``/
``retry`` → READY) with different legal sources. The plain
``state -> reachable states`` view is derived from the action tables, so the
two can never disagree.

Execution is the aggregate root: node transitions go through it so
cross-entity rules hold (no node starts while the execution is paused or
cancelling, an execution cannot complete with unfinished nodes, a terminal
execution has only terminal nodes, cancellation is never revived by a retry).

Retry/timeout *policies* live here as values; *enforcement* (sleeping for
backoff, watching deadlines) is the runtime's job (Phase 08+).
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum, auto
from math import inf
from typing import Final

from akasha import __version__
from akasha.domain.node import RETRYABLE_ERROR_KINDS, NodeError, NodeErrorKind
from akasha.domain.primitives import Entity, new_id, utcnow, validate_id
from akasha.domain.project import ProjectVersion
from akasha.domain.trigger import TriggerType
from akasha.errors import DomainValidationError, InvalidStateTransitionError


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


_E = ExecutionStatus
_N = NodeExecutionStatus

_E_ACTIVE: Final = frozenset({_E.CREATED, _E.QUEUED, _E.RUNNING, _E.PAUSED, _E.WAITING})
# Started and not finished: nodes may be in flight.
_E_STARTED: Final = frozenset({_E.RUNNING, _E.PAUSED, _E.WAITING})

# action -> (legal source states, target state)
EXECUTION_ACTIONS: Final[Mapping[str, tuple[frozenset[_E], _E]]] = {
    "enqueue": (frozenset({_E.CREATED}), _E.QUEUED),
    "start": (frozenset({_E.QUEUED}), _E.RUNNING),
    "pause": (frozenset({_E.RUNNING}), _E.PAUSED),
    "wait": (frozenset({_E.RUNNING}), _E.WAITING),
    "resume": (frozenset({_E.PAUSED, _E.WAITING}), _E.RUNNING),
    "complete": (frozenset({_E.RUNNING}), _E.COMPLETED),
    # Failure can strike before start (e.g. snapshot cannot be loaded) or
    # while paused/waiting (an in-flight node fails permanently).
    "fail": (_E_ACTIVE, _E.FAILED),
    "cancel": (_E_ACTIVE, _E.CANCELLED),
    # The execution deadline counts from start, so only started executions
    # can time out; paused/waiting time still counts (no immortal runs).
    "time_out": (_E_STARTED, _E.TIMEOUT),
}

NODE_ACTIONS: Final[Mapping[str, tuple[frozenset[_N], _N]]] = {
    "mark_ready": (frozenset({_N.PENDING}), _N.READY),
    "start": (frozenset({_N.READY}), _N.RUNNING),
    "retry": (frozenset({_N.RUNNING}), _N.READY),
    "complete": (frozenset({_N.RUNNING}), _N.COMPLETED),
    "fail": (frozenset({_N.RUNNING}), _N.FAILED),
    "skip": (frozenset({_N.PENDING, _N.READY}), _N.SKIPPED),
    "cancel": (frozenset({_N.PENDING, _N.READY, _N.RUNNING}), _N.CANCELLED),
}


def _derive[S: StrEnum](
    states: type[S], actions: Mapping[str, tuple[frozenset[S], S]]
) -> dict[S, frozenset[S]]:
    return {
        s: frozenset(target for sources, target in actions.values() if s in sources)
        for s in states
    }


EXECUTION_TRANSITIONS: Final = _derive(ExecutionStatus, EXECUTION_ACTIONS)
NODE_TRANSITIONS: Final = _derive(NodeExecutionStatus, NODE_ACTIONS)
TERMINAL_EXECUTION_STATUSES: Final = frozenset(
    s for s, targets in EXECUTION_TRANSITIONS.items() if not targets
)
TERMINAL_NODE_STATUSES: Final = frozenset(
    s for s, targets in NODE_TRANSITIONS.items() if not targets
)


def _illegal(
    message: str, *, action: str, current: StrEnum, **details: str
) -> InvalidStateTransitionError:
    return InvalidStateTransitionError(
        message, details={"action": action, "from": str(current), **details}
    )


def _apply[S: StrEnum](
    actions: Mapping[str, tuple[frozenset[S], S]], action: str, current: S
) -> S:
    sources, target = actions[action]
    if current not in sources:
        raise _illegal(
            f"illegal transition: {action} from {current}",
            action=action,
            current=current,
            to=str(target),
        )
    return target


# --------------------------------------------------------------------------
# Value objects
# --------------------------------------------------------------------------

_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


@dataclass(frozen=True)
class ExecutionError:
    """Persisted, structured failure record (not an exception).

    ``code`` is the stable machine-readable identifier; ``message`` is for
    humans only. ``kind`` reuses the node error classification so retry
    semantics derive from one taxonomy.
    """

    code: str
    message: str
    kind: NodeErrorKind
    node_id: str | None = None
    details: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _CODE_RE.match(self.code):
            raise DomainValidationError(f"malformed error code: {self.code!r}")
        if not self.message.strip():
            raise DomainValidationError("error message must not be blank")
        object.__setattr__(self, "kind", NodeErrorKind(self.kind))
        if self.node_id is not None:
            validate_id(self.node_id)
        # Defensive copy: callers must not mutate a recorded error.
        object.__setattr__(self, "details", dict(self.details))

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE_ERROR_KINDS

    @classmethod
    def from_node_error(cls, error: NodeError, *, node_id: str) -> "ExecutionError":
        return cls(
            code=error.code,
            message=error.message,
            kind=error.kind,
            node_id=node_id,
            details={k: str(v) for k, v in error.details.items()},
        )

    @classmethod
    def node_timeout(cls, *, node_id: str, timeout: timedelta) -> "ExecutionError":
        return cls(
            code="NODE_TIMEOUT",
            message=f"node exceeded its timeout of {timeout.total_seconds()}s",
            kind=NodeErrorKind.TIMEOUT,
            node_id=node_id,
        )


@dataclass(frozen=True)
class RetryPolicy:
    """Node retry policy (backend AGENTS §39).

    ``max_attempts`` counts *all* attempts including the first, so the
    default of 1 means "never retry": retrying side effects is not harmless
    (AGENTS §47), so it must be opted into.
    """

    max_attempts: int = 1
    initial_backoff: timedelta = timedelta(seconds=1)
    backoff_multiplier: float = 2.0
    max_backoff: timedelta = timedelta(minutes=5)
    retryable_kinds: frozenset[NodeErrorKind] = RETRYABLE_ERROR_KINDS

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise DomainValidationError("max_attempts must be >= 1")
        if self.initial_backoff < timedelta(0):
            raise DomainValidationError("initial_backoff must be >= 0")
        if self.backoff_multiplier < 1:
            raise DomainValidationError("backoff_multiplier must be >= 1")
        if self.max_backoff < self.initial_backoff:
            raise DomainValidationError("max_backoff must be >= initial_backoff")
        # Cancellation must never be revived by a retry, whatever the config.
        if NodeErrorKind.CANCELLATION in self.retryable_kinds:
            raise DomainValidationError("cancellation errors are never retryable")

    def allows_retry(self, error: ExecutionError, *, attempt: int) -> bool:
        """May a node whose ``attempt``-th attempt failed with ``error`` retry?"""
        return error.kind in self.retryable_kinds and attempt < self.max_attempts

    def backoff(self, attempt: int) -> timedelta:
        """Delay before attempt ``attempt + 1``: exponential, capped.

        ponytail: deterministic, no jitter; add jitter via an injected RNG
        when many workers retry the same provider in lock-step (Phase 22/23).
        """
        if attempt < 1:
            raise DomainValidationError("attempt must be >= 1")
        try:
            seconds = self.initial_backoff.total_seconds() * (
                self.backoff_multiplier ** (attempt - 1)
            )
        except OverflowError:
            seconds = inf
        return timedelta(seconds=min(seconds, self.max_backoff.total_seconds()))


@dataclass(frozen=True)
class TimeoutPolicy:
    """Wall-clock limits; ``None`` means unlimited (resource caps: Phase 24).

    The node timeout applies per attempt; the execution timeout from the
    first start, including paused/waiting time.
    """

    execution_timeout: timedelta | None = None
    node_timeout: timedelta | None = None

    def __post_init__(self) -> None:
        for limit in (self.execution_timeout, self.node_timeout):
            if limit is not None and limit <= timedelta(0):
                raise DomainValidationError("timeouts must be positive")

    @staticmethod
    def _deadline(start: datetime | None, limit: timedelta | None) -> datetime | None:
        return None if start is None or limit is None else start + limit

    def execution_deadline(self, started_at: datetime | None) -> datetime | None:
        return self._deadline(started_at, self.execution_timeout)

    def node_deadline(self, started_at: datetime | None) -> datetime | None:
        return self._deadline(started_at, self.node_timeout)


@dataclass(frozen=True)
class CancellationState:
    """A recorded cancellation request. Cooperative: the request is a signal
    to the runtime/nodes; ``Execution.cancel()`` records the final state."""

    requested_at: datetime
    reason: str = ""


@dataclass(frozen=True)
class ExecutionMetadata:
    """Reproducibility metadata captured at creation (AGENTS §73).

    Resolved node/plugin versions are added once the registry exists
    (Phase 09/17).
    """

    trigger_type: TriggerType = TriggerType.MANUAL
    trigger_id: str | None = None
    correlation_id: str | None = None
    engine_version: str = __version__

    def __post_init__(self) -> None:
        object.__setattr__(self, "trigger_type", TriggerType(self.trigger_type))
        if self.trigger_id is not None:
            validate_id(self.trigger_id)
        cid = self.correlation_id
        # Correlation ids come from request headers: keep them log-safe.
        if cid is not None and not (0 < len(cid) <= 128 and cid.isprintable()):
            raise DomainValidationError("correlation_id must be 1-128 printable chars")
        if not self.engine_version.strip():
            raise DomainValidationError("engine_version must not be blank")


# --------------------------------------------------------------------------
# Entities
# --------------------------------------------------------------------------


@dataclass(eq=False)
class NodeExecution(Entity):
    """State of one node instance within one execution.

    Retries reuse the same NodeExecution (RUNNING → READY); ``attempt``
    counts starts and ``failures`` records every failed attempt, so retry
    history survives a later success. Mutate only through ``Execution``.
    """

    node_id: str  # id of the NodeInstance in the pinned ProjectVersion
    status: NodeExecutionStatus = NodeExecutionStatus.PENDING
    attempt: int = 0
    failures: list[ExecutionError] = field(default_factory=list)
    retry_at: datetime | None = None
    started_at: datetime | None = None  # start of the current/last attempt
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        self.id = validate_id(self.id)
        validate_id(self.node_id)
        self.status = NodeExecutionStatus(self.status)
        # Rehydration must not smuggle in states the transitions can't reach.
        if self.attempt < len(self.failures):
            raise DomainValidationError("more failures than attempts")
        if self.status is _N.FAILED and not self.failures:
            raise DomainValidationError("failed node execution requires an error")

    @classmethod
    def for_node(cls, node_id: str) -> "NodeExecution":
        return cls(id=new_id("nexec"), node_id=node_id)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_NODE_STATUSES

    @property
    def error(self) -> ExecutionError | None:
        """The failure that ended the node, if it ended by failing."""
        return self.failures[-1] if self.status is _N.FAILED else None

    def _check_error(self, error: ExecutionError) -> None:
        if error.node_id not in (None, self.node_id):
            raise DomainValidationError(
                "error belongs to a different node",
                details={"node_id": self.node_id, "error_node_id": error.node_id},
            )

    def _finish(self, action: str, now: datetime | None) -> None:
        self.status = _apply(NODE_ACTIONS, action, self.status)
        self.finished_at = now or utcnow()

    def mark_ready(self) -> None:
        self.status = _apply(NODE_ACTIONS, "mark_ready", self.status)

    def start(self, *, now: datetime | None = None) -> None:
        now = now or utcnow()
        target = _apply(NODE_ACTIONS, "start", self.status)
        if self.retry_at is not None and now < self.retry_at:
            raise _illegal(
                "retry backoff has not elapsed",
                action="start",
                current=self.status,
                retry_at=self.retry_at.isoformat(),
            )
        self.status = target
        self.attempt += 1
        self.started_at = now
        self.retry_at = None

    def complete(self, *, now: datetime | None = None) -> None:
        self._finish("complete", now)

    def fail(self, error: ExecutionError, *, now: datetime | None = None) -> None:
        self._check_error(error)
        self._finish("fail", now)
        self.failures.append(error)

    def retry(self, error: ExecutionError, *, retry_at: datetime) -> None:
        """Record a failed attempt and schedule the next one. The *decision*
        (policy, cancellation) is made by ``Execution.retry_node``."""
        self._check_error(error)
        self.status = _apply(NODE_ACTIONS, "retry", self.status)
        self.failures.append(error)
        self.retry_at = retry_at

    def skip(self, *, now: datetime | None = None) -> None:
        # Skipping is a pre-run control-flow decision; a node awaiting a
        # retry has already run (and maybe had side effects).
        if self.attempt > 0:
            raise _illegal(
                "a node that already ran cannot be skipped",
                action="skip",
                current=self.status,
            )
        self._finish("skip", now)

    def cancel(self, *, now: datetime | None = None) -> None:
        self._finish("cancel", now)


@dataclass(eq=False)
class Execution(Entity):
    """One run of one immutable ProjectVersion snapshot (aggregate root).

    ``node_executions`` is keyed by node instance id and covers exactly the
    nodes of the pinned graph.
    """

    project_id: str
    project_version: ProjectVersion
    metadata: ExecutionMetadata = field(default_factory=ExecutionMetadata)
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    timeout_policy: TimeoutPolicy = field(default_factory=TimeoutPolicy)
    status: ExecutionStatus = ExecutionStatus.CREATED
    node_executions: dict[str, NodeExecution] = field(default_factory=dict)
    error: ExecutionError | None = None
    cancellation: CancellationState | None = None
    created_at: datetime = field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        self.id = validate_id(self.id)
        validate_id(self.project_id)
        self.status = ExecutionStatus(self.status)
        graph_nodes = self.project_version.graph.nodes
        for key, ne in self.node_executions.items():
            if key != ne.node_id or key not in graph_nodes:
                raise DomainValidationError(
                    "node execution does not match a node of the pinned version",
                    details={"node_id": key},
                )
        # Rehydration must not smuggle in states the transitions can't reach.
        if self.is_terminal and not all(
            ne.is_terminal for ne in self.node_executions.values()
        ):
            raise DomainValidationError("terminal execution with unfinished nodes")
        if self.status in (_E.FAILED, _E.TIMEOUT) and self.error is None:
            raise DomainValidationError(f"{self.status} execution requires an error")
        if self.status is _E.CANCELLED and self.cancellation is None:
            raise DomainValidationError("cancelled execution requires cancellation")

    @classmethod
    def create(
        cls,
        project_id: str,
        project_version: ProjectVersion,
        *,
        metadata: ExecutionMetadata | None = None,
        retry_policy: RetryPolicy | None = None,
        timeout_policy: TimeoutPolicy | None = None,
        now: datetime | None = None,
    ) -> "Execution":
        return cls(
            id=new_id("exec"),
            project_id=project_id,
            project_version=project_version,
            metadata=metadata or ExecutionMetadata(),
            retry_policy=retry_policy or RetryPolicy(),
            timeout_policy=timeout_policy or TimeoutPolicy(),
            node_executions={
                node_id: NodeExecution.for_node(node_id)
                for node_id in project_version.graph.nodes
            },
            created_at=now or utcnow(),
        )

    # -- queries ------------------------------------------------------------

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_EXECUTION_STATUSES

    @property
    def is_cancellation_requested(self) -> bool:
        """What ``NodeExecutionContext.is_cancelled`` reflects to nodes."""
        return self.cancellation is not None

    def is_timed_out(self, now: datetime) -> bool:
        deadline = self.timeout_policy.execution_deadline(self.started_at)
        return deadline is not None and now >= deadline

    def node(self, node_id: str) -> NodeExecution:
        try:
            return self.node_executions[node_id]
        except KeyError:
            raise DomainValidationError(
                "unknown node in this execution", details={"node_id": node_id}
            ) from None

    # -- execution transitions ----------------------------------------------

    def _target(self, action: str) -> ExecutionStatus:
        """Validate ``action`` without mutating anything."""
        target = _apply(EXECUTION_ACTIONS, action, self.status)
        # After a cancellation request the only way out is CANCELLED: a run
        # that was asked to stop must never report success, pause or resume.
        if self.cancellation is not None and action != "cancel":
            raise _illegal(
                "cancellation has been requested",
                action=action,
                current=self.status,
                to=str(target),
            )
        return target

    def _finish(self, action: str, now: datetime | None) -> None:
        """Enter a terminal state; every unfinished node is cancelled so a
        terminal execution never has live nodes."""
        target = self._target(action)
        now = now or utcnow()
        for ne in self.node_executions.values():
            if not ne.is_terminal:
                ne.cancel(now=now)
        self.status = target
        self.finished_at = now

    def enqueue(self) -> None:
        self.status = self._target("enqueue")

    def start(self, *, now: datetime | None = None) -> None:
        self.status = self._target("start")
        self.started_at = now or utcnow()

    def pause(self) -> None:
        self.status = self._target("pause")

    def wait(self) -> None:
        self.status = self._target("wait")

    def resume(self) -> None:
        self.status = self._target("resume")

    def complete(self, *, now: datetime | None = None) -> None:
        target = self._target("complete")
        unfinished = sorted(
            ne.node_id
            for ne in self.node_executions.values()
            if ne.status not in (_N.COMPLETED, _N.SKIPPED)
        )
        if unfinished:
            raise _illegal(
                "cannot complete: nodes not completed or skipped",
                action="complete",
                current=self.status,
                to=str(target),
                node_ids=",".join(unfinished),
            )
        self._finish("complete", now)

    def fail(self, error: ExecutionError, *, now: datetime | None = None) -> None:
        self._target("fail")
        if error.node_id is not None:
            self.node(error.node_id)
        self._finish("fail", now)
        self.error = error

    def request_cancellation(
        self, *, reason: str = "", now: datetime | None = None
    ) -> None:
        """Signal cooperative cancellation. Idempotent: duplicate requests
        (double clicks, redelivered commands) keep the first request."""
        if self.is_terminal:
            raise _illegal(
                "execution already finished",
                action="request_cancellation",
                current=self.status,
            )
        if self.cancellation is None:
            self.cancellation = CancellationState(now or utcnow(), reason)

    def cancel(self, *, reason: str = "", now: datetime | None = None) -> None:
        self._target("cancel")
        now = now or utcnow()
        if self.cancellation is None:
            self.cancellation = CancellationState(now, reason)
        self._finish("cancel", now)

    def time_out(self, *, now: datetime | None = None) -> None:
        self._target("time_out")
        self._finish("time_out", now)
        self.error = ExecutionError(
            code="EXECUTION_TIMEOUT",
            message="execution exceeded its timeout",
            kind=NodeErrorKind.TIMEOUT,
        )

    # -- node transitions (aggregate-guarded) --------------------------------

    def _node_for(
        self,
        node_id: str,
        action: str,
        allowed: frozenset[ExecutionStatus],
        *,
        blocked_by_cancellation: bool,
    ) -> NodeExecution:
        ne = self.node(node_id)
        if self.status not in allowed:
            raise _illegal(
                f"node {action} not allowed while execution is {self.status}",
                action=f"node.{action}",
                current=self.status,
                node_id=node_id,
            )
        if blocked_by_cancellation and self.cancellation is not None:
            raise _illegal(
                "cancellation has been requested",
                action=f"node.{action}",
                current=self.status,
                node_id=node_id,
            )
        return ne

    def mark_node_ready(self, node_id: str) -> None:
        # Readiness may be resolved while paused; *starting* may not.
        self._node_for(
            node_id, "mark_ready", _E_STARTED, blocked_by_cancellation=True
        ).mark_ready()

    def start_node(self, node_id: str, *, now: datetime | None = None) -> None:
        self._node_for(
            node_id, "start", frozenset({_E.RUNNING}), blocked_by_cancellation=True
        ).start(now=now)

    def skip_node(self, node_id: str, *, now: datetime | None = None) -> None:
        self._node_for(node_id, "skip", _E_STARTED, blocked_by_cancellation=True).skip(
            now=now
        )

    # In-flight nodes may finish after a pause or cancellation request: the
    # work (and its side effects) really happened, so the record stays true.
    def complete_node(self, node_id: str, *, now: datetime | None = None) -> None:
        self._node_for(
            node_id, "complete", _E_STARTED, blocked_by_cancellation=False
        ).complete(now=now)

    def fail_node(
        self, node_id: str, error: ExecutionError, *, now: datetime | None = None
    ) -> None:
        self._node_for(node_id, "fail", _E_STARTED, blocked_by_cancellation=False).fail(
            error, now=now
        )

    def retry_node(
        self, node_id: str, error: ExecutionError, *, now: datetime | None = None
    ) -> timedelta:
        """Schedule another attempt if the policy allows; returns the backoff.

        Rejected (state unchanged) when the error is not retryable, attempts
        are exhausted, or cancellation was requested — the caller then
        records the failure with ``fail_node``.
        """
        ne = self._node_for(node_id, "retry", _E_STARTED, blocked_by_cancellation=True)
        _apply(NODE_ACTIONS, "retry", ne.status)
        if not self.retry_policy.allows_retry(error, attempt=ne.attempt):
            raise _illegal(
                "retry not allowed by policy",
                action="node.retry",
                current=ne.status,
                node_id=node_id,
                kind=str(error.kind),
                attempt=str(ne.attempt),
                max_attempts=str(self.retry_policy.max_attempts),
            )
        delay = self.retry_policy.backoff(ne.attempt)
        ne.retry(error, retry_at=(now or utcnow()) + delay)
        return delay
