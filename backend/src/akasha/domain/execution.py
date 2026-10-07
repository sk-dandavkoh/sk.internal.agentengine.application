"""Execution and NodeExecution: WHAT happened / is happening.

An Execution always pins an immutable WorkflowVersion snapshot — never a draft
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

Lifecycle events (Phase 11) are recorded by the transitions themselves, so no
caller can change state without its event: ``pending_events`` accumulates
them until persistence stores them in the same transaction as the state.
Rehydrating an execution records nothing. Not evented: enqueue (folded into
``execution.created``), mark_ready and pause/resume — no runtime path drives
those two yet.

Waiting (core node portfolio): a node attempt may end in ``wait_node`` (the
node needs outside input, e.g. a person's answer). Once nothing else can run,
the runtime moves the execution to WAITING: persisted, owned by no worker, so
it survives restarts and holds no process. ``resume_node`` (a response) or
``expire_waits`` (a deadline) makes the waiting node READY again and the
execution QUEUED (``wake``); a worker then resumes it like a recovered run.

Recovery (Phase 23): ``recover`` takes over a started execution whose owner
died. Work that finished stays finished; the attempts the owner left
RUNNING are the only unknowns — their side effects may or may not have
happened — so each is settled explicitly (``recover``) before the execution
is QUEUED again. A worker then resumes it (``start`` keeps the original
start time, so the execution deadline still counts from the first start).
"""

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum, auto
from math import inf
from typing import Any, Final

from akasha import __version__
from akasha.domain.event import Event, EventType, PayloadValue
from akasha.domain.graph import Edge
from akasha.domain.node import (
    RETRYABLE_ERROR_KINDS,
    NodeConfigSchema,
    NodeError,
    NodeErrorKind,
)
from akasha.domain.primitives import (
    Entity,
    is_storable,
    new_id,
    storable_text,
    utcnow,
    validate_id,
)
from akasha.domain.trigger import TriggerType
from akasha.domain.workflow import WorkflowVersion
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
    WAITING = auto()  # an attempt asked for outside input (``wait_node``)
    COMPLETED = auto()
    FAILED = auto()
    SKIPPED = auto()
    CANCELLED = auto()


_E = ExecutionStatus
_N = NodeExecutionStatus

_E_ACTIVE: Final = frozenset({_E.CREATED, _E.QUEUED, _E.RUNNING, _E.PAUSED, _E.WAITING})
# Started and not finished: nodes may be in flight.
_E_STARTED: Final = frozenset({_E.RUNNING, _E.PAUSED, _E.WAITING})
_N_SETTLED: Final = frozenset(set(_N) - {_N.RUNNING})

# action -> (legal source states, target state)
EXECUTION_ACTIONS: Final[Mapping[str, tuple[frozenset[_E], _E]]] = {
    "enqueue": (frozenset({_E.CREATED}), _E.QUEUED),
    # Owner lost (stale heartbeat): back to the queue to be resumed.
    "recover": (_E_STARTED, _E.QUEUED),
    "start": (frozenset({_E.QUEUED}), _E.RUNNING),
    "pause": (frozenset({_E.RUNNING}), _E.PAUSED),
    "wait": (frozenset({_E.RUNNING}), _E.WAITING),
    "resume": (frozenset({_E.PAUSED, _E.WAITING}), _E.RUNNING),
    # A waiting node was answered or expired: back to the queue, owner-less
    # until a worker resumes it (``start`` keeps the first start time).
    "wake": (frozenset({_E.WAITING}), _E.QUEUED),
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
    "wait": (frozenset({_N.RUNNING}), _N.WAITING),
    # The answered (or expired) node runs again with the response.
    "resume": (frozenset({_N.WAITING}), _N.READY),
    "cancel": (
        frozenset({_N.PENDING, _N.READY, _N.RUNNING, _N.WAITING}),
        _N.CANCELLED,
    ),
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

NODE_INTERRUPTED: Final = "NODE_INTERRUPTED"
# Attempts in all a side-effect-free node gets when its owner keeps dying
# mid-attempt without a retry policy. The bound stops a node that kills its
# worker (OOM, segfault) from being recovered forever.
INTERRUPTED_ATTEMPTS_LIMIT: Final = 3


class LogLevel(StrEnum):
    """Python's level names; the order is severity (``at_least``)."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

    def at_least(self) -> tuple["LogLevel", ...]:
        levels = tuple(LogLevel)
        return levels[levels.index(self) :]


@dataclass(frozen=True)
class NodeLogEntry:
    """One log line a node attempt wrote through ``context.logger``.

    History next to the state, like events, but free text: the runtime
    redacts and bounds it before recording (``akasha.runtime.node_logs``).
    ``position`` is assigned by the store and orders one execution's logs.
    """

    execution_id: str
    node_id: str
    attempt: int
    level: LogLevel
    message: str
    logged_at: datetime
    fields: Mapping[str, Any] = field(default_factory=dict)
    position: int | None = None

    def __post_init__(self) -> None:
        validate_id(self.execution_id)
        validate_id(self.node_id)
        object.__setattr__(self, "level", LogLevel(self.level))
        if self.attempt < 1:
            raise DomainValidationError("a log line belongs to an attempt >= 1")
        if self.logged_at.tzinfo is None:
            raise DomainValidationError("log time must be timezone-aware")
        object.__setattr__(self, "message", storable_text(self.message))
        fields = dict(self.fields)
        if not is_storable(fields):
            raise DomainValidationError("log fields must be storable JSON")
        object.__setattr__(self, "fields", fields)


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
        # Unstorable characters are replaced, not rejected: messages and
        # details are for humans, and a failing node must still be able to
        # record its failure. The dict is also a defensive copy.
        object.__setattr__(self, "message", storable_text(self.message))
        object.__setattr__(
            self,
            "details",
            {storable_text(k): storable_text(v) for k, v in self.details.items()},
        )

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE_ERROR_KINDS

    @classmethod
    def from_node_error(cls, error: NodeError, *, node_id: str) -> "ExecutionError":
        """A node's error is untrusted input (plugins included): whatever
        breaks the ExecutionError rules falls back to the generic values,
        so the failure is recorded instead of aborting the run."""
        try:
            kind = NodeErrorKind(error.kind)
        except ValueError:
            kind = NodeErrorKind.PERMANENT
        code = error.code if _CODE_RE.match(str(error.code)) else NodeError.code
        return cls(
            code=code,
            message=error.message
            if error.message.strip()
            else "node failed without a message",
            kind=kind,
            node_id=node_id,
            details={str(k): str(v) for k, v in error.details.items()},
        )

    @classmethod
    def node_interrupted(cls, *, node_id: str) -> "ExecutionError":
        # TRANSIENT: the attempt did not fail on its own; repeating it may
        # well succeed — whether it *may* be repeated is ``recover``'s call.
        return cls(
            code=NODE_INTERRUPTED,
            message="the process running this node attempt was lost; "
            "its side effects may or may not have happened",
            kind=NodeErrorKind.TRANSIENT,
            node_id=node_id,
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
    """Wall-clock limits; ``None`` means unlimited here. Executions admitted
    through ``ExecutionService`` never keep ``None``: it is replaced by the
    engine cap (``akasha.domain.limits.ResourceLimits``).

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
class WaitState:
    """What a waiting node asks for (``NodeWait`` as persisted): the prompt
    shown to whoever answers, the JSON-Schema object (config-schema subset)
    a response must satisfy, and when the wait expires (None: never; the
    execution deadline still applies)."""

    prompt: str
    response_schema: Mapping[str, Any]
    deadline: datetime | None = None

    def __post_init__(self) -> None:
        if not self.prompt.strip():
            raise DomainValidationError("a wait prompt must not be blank")
        if not is_storable(dict(self.response_schema)):
            raise DomainValidationError("a response schema must be JSON")
        # Fails on a schema the validator cannot apply, at wait time.
        NodeConfigSchema(dict(self.response_schema))

    def issues(self, response: Mapping[str, Any]) -> list[str]:
        """Why ``response`` does not answer this wait (empty: it does)."""
        schema = NodeConfigSchema(dict(self.response_schema))
        return [f"{i.location} {i.message}" for i in schema.issues(response)]


@dataclass(frozen=True)
class ExecutionMetadata:
    """Reproducibility metadata (AGENTS §73).

    Captured at creation, except ``node_versions``: the node type → version
    map the runtime actually resolved, recorded when the execution starts
    (``Execution.start``). Plugin versions follow with plugins (Phase 17).
    """

    trigger_type: TriggerType = TriggerType.MANUAL
    trigger_id: str | None = None
    correlation_id: str | None = None
    engine_version: str = __version__
    node_versions: Mapping[str, str] = field(default_factory=dict)
    # Event-trigger hops that led to this run (``TriggerContext.depth``).
    trigger_depth: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "trigger_type", TriggerType(self.trigger_type))
        if isinstance(self.trigger_depth, bool) or self.trigger_depth < 0:
            raise DomainValidationError("trigger_depth must be >= 0")
        object.__setattr__(self, "node_versions", dict(self.node_versions))
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

    node_id: str  # id of the NodeInstance in the pinned WorkflowVersion
    status: NodeExecutionStatus = NodeExecutionStatus.PENDING
    attempt: int = 0
    failures: list[ExecutionError] = field(default_factory=list)
    retry_at: datetime | None = None
    started_at: datetime | None = None  # start of the current/last attempt
    finished_at: datetime | None = None
    # Values keyed by output port; set exactly when the node COMPLETED.
    outputs: dict[str, Any] | None = None
    # The last wait request (kept after it was answered: audit) and the
    # answer the next attempt receives as ``context.resume``.
    waiting_for: WaitState | None = None
    resume_input: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.id = validate_id(self.id)
        validate_id(self.node_id)
        self.status = NodeExecutionStatus(self.status)
        # Rehydration must not smuggle in states the transitions can't reach.
        if self.attempt < len(self.failures):
            raise DomainValidationError("more failures than attempts")
        # Only ``retry`` leads back to READY and it records the failure, so
        # every attempt but the last failed; reports number failures by it.
        if len(self.failures) < self.attempt - 1:
            raise DomainValidationError("an earlier attempt has no failure")
        if self.status is _N.FAILED and not self.failures:
            raise DomainValidationError("failed node execution requires an error")
        if self.outputs is not None and self.status is not _N.COMPLETED:
            raise DomainValidationError("only a completed node has outputs")
        if self.status is _N.WAITING and self.waiting_for is None:
            raise DomainValidationError("a waiting node requires its wait request")

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
        # Answering a wait continues the attempt that asked: it is not a
        # retry (reports count ``attempt - 1`` retries) and does not use up
        # the retry policy. ``retry`` always sets ``retry_at``; ``resume``
        # never does, so a READY node with a response and no backoff is a
        # resumed one.
        if self.resume_input is None or self.retry_at is not None:
            self.attempt += 1
        self.started_at = now
        self.retry_at = None

    def complete(
        self, *, outputs: Mapping[str, Any] | None = None, now: datetime | None = None
    ) -> None:
        self._finish("complete", now)
        self.outputs = dict(outputs or {})

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

    def wait(self, wait: WaitState) -> None:
        self.status = _apply(NODE_ACTIONS, "wait", self.status)
        self.waiting_for = wait
        self.resume_input = None

    def resume(self, resume: Mapping[str, Any]) -> None:
        self.status = _apply(NODE_ACTIONS, "resume", self.status)
        self.resume_input = deepcopy(dict(resume))

    def cancel(self, *, now: datetime | None = None) -> None:
        self._finish("cancel", now)


@dataclass(eq=False)
class Execution(Entity):
    """One run of one immutable WorkflowVersion snapshot (aggregate root).

    ``node_executions`` is keyed by node instance id and covers exactly the
    nodes of the pinned graph.
    """

    workflow_id: str
    workflow_version: WorkflowVersion
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
    # Optimistic-concurrency token of the persisted row (0 = never
    # persisted); advanced by persistence, only read by the domain.
    revision: int = 0
    # The JSON object the execution was started with (manual/API/trigger
    # payload); entry nodes read it via NodeExecutionContext.execution_input.
    input: dict[str, Any] = field(default_factory=dict)
    # Events recorded since the last ``pull_events`` (never persisted state).
    _events: list[Event] = field(default_factory=list, init=False, repr=False)
    # Node log lines recorded since the last ``pull_logs``; stored with the
    # next save, like events.
    _logs: list[NodeLogEntry] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self.id = validate_id(self.id)
        validate_id(self.workflow_id)
        self.status = ExecutionStatus(self.status)
        if self.revision < 0:
            raise DomainValidationError("revision must be >= 0")
        if not isinstance(self.input, dict) or not is_storable(self.input):
            raise DomainValidationError("execution input must be a JSON object")
        graph_nodes = self.workflow_version.graph.nodes
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
        if self.status in (_E.CREATED, _E.QUEUED):
            # A recovered execution (started before) keeps its progress, but
            # ``recover`` settled every in-flight attempt.
            allowed = {_N.PENDING} if self.started_at is None else _N_SETTLED
            if any(ne.status not in allowed for ne in self.node_executions.values()):
                raise DomainValidationError("queued execution with progressed nodes")
        if self.status in (_E.FAILED, _E.TIMEOUT) and self.error is None:
            raise DomainValidationError(f"{self.status} execution requires an error")
        if self.status is _E.CANCELLED and self.cancellation is None:
            raise DomainValidationError("cancelled execution requires cancellation")

    @classmethod
    def create(
        cls,
        workflow_id: str,
        workflow_version: WorkflowVersion,
        *,
        metadata: ExecutionMetadata | None = None,
        retry_policy: RetryPolicy | None = None,
        timeout_policy: TimeoutPolicy | None = None,
        input: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> "Execution":
        execution = cls(
            id=new_id("exec"),
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            metadata=metadata or ExecutionMetadata(),
            retry_policy=retry_policy or RetryPolicy(),
            timeout_policy=timeout_policy or TimeoutPolicy(),
            node_executions={
                node_id: NodeExecution.for_node(node_id)
                for node_id in workflow_version.graph.nodes
            },
            created_at=now or utcnow(),
            # Deep copy: the caller's payload must not alias the record.
            input=deepcopy(dict(input or {})),
        )
        execution._emit(
            EventType.EXECUTION_CREATED,
            execution.created_at,
            trigger_type=execution.metadata.trigger_type.value,
            trigger_id=execution.metadata.trigger_id,
        )
        return execution

    # -- events ---------------------------------------------------------------

    @property
    def pending_events(self) -> tuple[Event, ...]:
        return tuple(self._events)

    def pull_events(self) -> list[Event]:
        """Hand over the recorded events (oldest first) and forget them."""
        events, self._events = self._events, []
        return events

    @property
    def pending_logs(self) -> tuple[NodeLogEntry, ...]:
        return tuple(self._logs)

    def pull_logs(self) -> list[NodeLogEntry]:
        """Hand over the recorded log lines and forget them."""
        logs, self._logs = self._logs, []
        return logs

    def _emit(
        self,
        event_type: EventType,
        now: datetime,
        *,
        node_id: str | None = None,
        **payload: PayloadValue,
    ) -> None:
        self._events.append(
            Event(
                event_type=event_type,
                occurred_at=now,
                workflow_id=self.workflow_id,
                execution_id=self.id,
                node_id=node_id,
                workflow_version=self.workflow_version.version,
                correlation_id=self.metadata.correlation_id,
                payload=payload,
            )
        )

    # -- queries ------------------------------------------------------------

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_EXECUTION_STATUSES

    @property
    def is_cancellation_requested(self) -> bool:
        """What ``NodeExecutionContext.is_cancelled`` reflects to nodes."""
        return self.cancellation is not None

    @property
    def is_resumed(self) -> bool:
        """Queued again by ``recover`` after it had started."""
        return self.status is _E.QUEUED and self.started_at is not None

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

    def delivers(self, edge: Edge) -> bool:
        """Whether ``edge`` carries a value: its source completed and
        produced that output port (an if/switch fills only the taken one).
        The one rule for what reached a node: the runtime builds inputs from
        it, reports show it."""
        source = self.node(edge.source_node_id)
        return source.status is NodeExecutionStatus.COMPLETED and (
            edge.source_port in (source.outputs or {})
        )

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

    def _finish(self, action: str, now: datetime | None) -> datetime:
        """Enter a terminal state; every unfinished node is cancelled so a
        terminal execution never has live nodes. Returns the finish time."""
        target = self._target(action)
        now = now or utcnow()
        for node_id in sorted(self.node_executions):  # deterministic event order
            ne = self.node_executions[node_id]
            if not ne.is_terminal:
                ne.cancel(now=now)
                self._emit(EventType.NODE_CANCELLED, now, node_id=node_id)
        self.status = target
        self.finished_at = now
        return now

    def enqueue(self) -> None:
        self.status = self._target("enqueue")

    def start(
        self,
        *,
        now: datetime | None = None,
        node_versions: Mapping[str, str] | None = None,
    ) -> None:
        """``node_versions``: the node type → version map resolved for this
        run, recorded for reproducibility (AGENTS §73).

        Resuming a recovered execution keeps the first start time and the
        recorded versions; the caller must refuse a resume whose resolved
        versions differ (``is_resumed``)."""
        target = self._target("start")
        now = now or utcnow()
        if self.started_at is not None:
            self.status = target
            self._emit(EventType.EXECUTION_RESUMED, now)
            return
        self.status = target
        self.started_at = now
        if node_versions is not None:
            self.metadata = replace(self.metadata, node_versions=node_versions)
        self._emit(EventType.EXECUTION_STARTED, self.started_at)

    def pause(self) -> None:
        self.status = self._target("pause")

    def wait(self, *, now: datetime | None = None) -> None:
        """Park the execution: no node runs or can run, at least one waits
        for outside input. The runtime calls it; bare transitions (tests)
        need no waiting node."""
        self.status = self._target("wait")
        if any(ne.status is _N.WAITING for ne in self.node_executions.values()):
            self._emit(EventType.EXECUTION_WAITING, now or utcnow())

    @property
    def wake_at(self) -> datetime | None:
        """When a WAITING execution must be looked at again without a
        response: the earliest wait deadline or the execution deadline."""
        if self.status is not _E.WAITING:
            return None
        times = [
            ne.waiting_for.deadline
            for ne in self.node_executions.values()
            if ne.status is _N.WAITING
            and ne.waiting_for is not None
            and ne.waiting_for.deadline is not None
        ]
        if (
            deadline := self.timeout_policy.execution_deadline(self.started_at)
        ) is not None:
            times.append(deadline)
        return min(times, default=None)

    def resume_node(
        self,
        node_id: str,
        response: Mapping[str, Any],
        *,
        responded_by: str | None = None,
        now: datetime | None = None,
    ) -> None:
        """Answer a waiting node and queue the execution to continue.

        Only while the execution is WAITING: then no worker owns it, so this
        CAS-guarded write cannot race a running scheduler loop. A second
        answer finds the node READY and is refused (no duplicate resume).
        """
        ne = self._waiting_node(node_id)
        if ne.waiting_for is not None and (problems := ne.waiting_for.issues(response)):
            raise DomainValidationError(
                "the response does not match what the node asks for",
                details={"issues": "; ".join(problems[:10])},
            )
        self._resume(
            ne,
            {
                "response": dict(response),
                "responded_by": responded_by,
                "timed_out": False,
            },
            now or utcnow(),
        )
        self.wake()

    def wake(self) -> None:
        """Queue a WAITING execution to continue (``resume_node`` and
        ``expire_waits`` call it after making a waiting node READY)."""
        self.status = self._target("wake")

    def expire_waits(self, *, now: datetime | None = None) -> bool:
        """Settle what is due on a WAITING execution: past the execution
        deadline it times out; a node whose wait deadline passed is resumed
        with ``timed_out`` and the execution is queued. Returns whether the
        state changed."""
        now = now or utcnow()
        if self.status is not _E.WAITING:
            return False
        if self.cancellation is not None:
            self.cancel(now=now)
            return True
        if self.is_timed_out(now):
            self.time_out(now=now)
            return True
        due = sorted(
            node_id
            for node_id, ne in self.node_executions.items()
            if ne.status is _N.WAITING
            and ne.waiting_for is not None
            and ne.waiting_for.deadline is not None
            and ne.waiting_for.deadline <= now
        )
        for node_id in due:
            self._resume(
                self.node_executions[node_id],
                {"response": None, "responded_by": None, "timed_out": True},
                now,
            )
        if due:
            self.wake()
        return bool(due)

    def _waiting_node(self, node_id: str) -> NodeExecution:
        ne = self.node(node_id)
        if self.status is not _E.WAITING or ne.status is not _N.WAITING:
            raise _illegal(
                "the node is not waiting for a response",
                action="node.resume",
                current=ne.status,
                node_id=node_id,
                execution_status=str(self.status),
            )
        self._target("wake")  # refuses after a cancellation request
        return ne

    def _resume(
        self, ne: NodeExecution, resume: Mapping[str, Any], now: datetime
    ) -> None:
        ne.resume(resume)
        self._emit(
            EventType.NODE_RESUMED,
            now,
            node_id=ne.node_id,
            outcome="timed_out" if resume["timed_out"] else "responded",
        )

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
        self._emit(EventType.EXECUTION_COMPLETED, self._finish("complete", now))

    def fail(self, error: ExecutionError, *, now: datetime | None = None) -> None:
        self._target("fail")
        if error.node_id is not None:
            self.node(error.node_id)
        finished = self._finish("fail", now)
        self.error = error
        self._emit(
            EventType.EXECUTION_FAILED,
            finished,
            error_code=error.code,
            error_kind=error.kind.value,
            error_node_id=error.node_id,
        )

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
        self._emit(EventType.EXECUTION_CANCELLED, now)

    def time_out(self, *, now: datetime | None = None) -> None:
        self._target("time_out")
        finished = self._finish("time_out", now)
        self.error = ExecutionError(
            code="EXECUTION_TIMEOUT",
            message="execution exceeded its timeout",
            kind=NodeErrorKind.TIMEOUT,
        )
        self._emit(EventType.EXECUTION_TIMED_OUT, finished, error_code=self.error.code)

    def recover(
        self, *, repeatable: frozenset[str], now: datetime | None = None
    ) -> None:
        """Take over a started execution whose owner is gone.

        Deterministic outcome, in this precedence:

        1. cancellation was requested → CANCELLED (never revived);
        2. the execution deadline passed → TIMEOUT;
        3. each attempt left RUNNING is settled as ``NODE_INTERRUPTED``:
           it is repeated if the retry policy allows it (the owner opted
           into repeats, side effects included) or if its node type is in
           ``repeatable`` (declared side-effect free) and has had fewer than
           ``INTERRUPTED_ATTEMPTS_LIMIT`` attempts; otherwise it FAILS and so
           does the execution — a possibly half-done side effect is never
           silently repeated;
        4. otherwise → QUEUED, to be resumed where it stopped.

        ``repeatable``: node *instance* ids whose node type has no side
        effects (the caller knows the node definitions, the aggregate not).
        """
        target = _apply(EXECUTION_ACTIONS, "recover", self.status)
        now = now or utcnow()
        if self.cancellation is not None:
            self.cancel(now=now)
            return
        if self.is_timed_out(now):
            self.time_out(now=now)
            return
        interrupted = sorted(
            node_id
            for node_id, ne in self.node_executions.items()
            if ne.status is _N.RUNNING
        )
        failure: ExecutionError | None = None
        for node_id in interrupted:
            ne = self.node_executions[node_id]
            error = ExecutionError.node_interrupted(node_id=node_id)
            if self.retry_policy.allows_retry(error, attempt=ne.attempt) or (
                node_id in repeatable and ne.attempt < INTERRUPTED_ATTEMPTS_LIMIT
            ):
                self._schedule_retry(ne, error, timedelta(0), now)
            else:
                self.fail_node(node_id, error, now=now)
                failure = failure or error
        if failure is not None:
            self.fail(failure, now=now)
            return
        self.status = target
        self._emit(
            EventType.EXECUTION_RECOVERED, now, interrupted_nodes=len(interrupted)
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
        ne = self._node_for(
            node_id, "start", frozenset({_E.RUNNING}), blocked_by_cancellation=True
        )
        now = now or utcnow()
        ne.start(now=now)
        self._emit(EventType.NODE_STARTED, now, node_id=node_id, attempt=ne.attempt)

    def record_node_event(
        self, node_id: str, name: str, *, now: datetime | None = None
    ) -> None:
        """A named event the running node emitted (``NodeEvents``); the
        runtime records it before the attempt's outcome. The name is checked
        against the node definition by ``NodeEvents``, not here."""
        ne = self._node_for(node_id, "emit", _E_STARTED, blocked_by_cancellation=False)
        if ne.status is not NodeExecutionStatus.RUNNING:
            raise _illegal(
                "only a running node can emit events",
                action="node.emit",
                current=ne.status,
                node_id=node_id,
            )
        self._emit(
            EventType.NODE_EMITTED,
            now or utcnow(),
            node_id=node_id,
            name=name,
            attempt=ne.attempt,
        )

    def record_node_logs(
        self,
        node_id: str,
        lines: Sequence[tuple[LogLevel, str, datetime, Mapping[str, Any]]],
    ) -> None:
        """What the running node logged in its current attempt; the runtime
        records it before the attempt's outcome, like ``record_node_event``."""
        ne = self._node_for(node_id, "log", _E_STARTED, blocked_by_cancellation=False)
        if ne.status is not NodeExecutionStatus.RUNNING:
            raise _illegal(
                "only a running node can log",
                action="node.log",
                current=ne.status,
                node_id=node_id,
            )
        self._logs.extend(
            NodeLogEntry(self.id, node_id, ne.attempt, level, message, at, fields)
            for level, message, at, fields in lines
        )

    def wait_node(
        self, node_id: str, wait: WaitState, *, now: datetime | None = None
    ) -> None:
        """The running attempt asked for outside input (``NodeWait``)."""
        ne = self._node_for(node_id, "wait", _E_STARTED, blocked_by_cancellation=False)
        ne.wait(wait)
        self._emit(
            EventType.NODE_WAITING, now or utcnow(), node_id=node_id, attempt=ne.attempt
        )

    def skip_node(self, node_id: str, *, now: datetime | None = None) -> None:
        ne = self._node_for(node_id, "skip", _E_STARTED, blocked_by_cancellation=True)
        now = now or utcnow()
        ne.skip(now=now)
        self._emit(EventType.NODE_SKIPPED, now, node_id=node_id)

    # In-flight nodes may finish after a pause or cancellation request: the
    # work (and its side effects) really happened, so the record stays true.
    def complete_node(
        self,
        node_id: str,
        *,
        outputs: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> None:
        ne = self._node_for(
            node_id, "complete", _E_STARTED, blocked_by_cancellation=False
        )
        now = now or utcnow()
        ne.complete(outputs=outputs, now=now)
        self._emit(EventType.NODE_COMPLETED, now, node_id=node_id, attempt=ne.attempt)

    def fail_node(
        self, node_id: str, error: ExecutionError, *, now: datetime | None = None
    ) -> None:
        ne = self._node_for(node_id, "fail", _E_STARTED, blocked_by_cancellation=False)
        now = now or utcnow()
        ne.fail(error, now=now)
        self._emit(
            EventType.NODE_FAILED,
            now,
            node_id=node_id,
            attempt=ne.attempt,
            error_code=error.code,
            error_kind=error.kind.value,
        )

    def can_retry_node(self, node_id: str, error: ExecutionError) -> bool:
        """Would ``retry_node`` accept this failed attempt?"""
        ne = self.node(node_id)
        return (
            self.status in _E_STARTED
            and self.cancellation is None
            and ne.status is _N.RUNNING
            and self.retry_policy.allows_retry(error, attempt=ne.attempt)
        )

    def retry_node(
        self, node_id: str, error: ExecutionError, *, now: datetime | None = None
    ) -> timedelta:
        """Schedule another attempt if the policy allows; returns the backoff.

        Rejected (state unchanged) when the error is not retryable, attempts
        are exhausted, or cancellation was requested — the caller then
        records the failure with ``fail_node`` (``can_retry_node`` asks
        first).
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
        self._schedule_retry(ne, error, delay, now or utcnow())
        return delay

    def _schedule_retry(
        self, ne: NodeExecution, error: ExecutionError, delay: timedelta, now: datetime
    ) -> None:
        ne.retry(error, retry_at=now + delay)
        self._emit(
            EventType.NODE_RETRY_SCHEDULED,
            now,
            node_id=ne.node_id,
            attempt=ne.attempt,
            error_code=error.code,
            error_kind=error.kind.value,
            backoff_ms=round(delay.total_seconds() * 1000),
        )
