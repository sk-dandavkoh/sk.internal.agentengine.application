"""ExecutionRuntime: Scheduler loop + StateManager.

Concurrency model (the reason this is race-free by construction):

- One coroutine — the scheduler loop in ``_schedule`` — owns the
  ``Execution`` aggregate. Only it mutates domain state and persists it.
- Node attempts run as tasks that only compute a ``NodeOutcome``; they
  never touch the aggregate or the database. ``_schedule`` cancels and
  awaits every one of them before it returns, whatever ended it (failure,
  persistence error, outer cancellation), so no node task outlives a run.
- Every save is a compare-and-swap on ``Execution.revision``: a second
  runtime (or an out-of-process cancel) racing the owner makes one side
  fail loudly instead of interleaving state.
- A node is persisted RUNNING *before* its task is created, so the stored
  state never hides that a node may have produced side effects.
- Lifecycle events are recorded by the aggregate's transitions, stored in
  the same transaction as each save and published after its commit. A
  failing event subscriber cannot affect the run (``EventPublisher``).
- Observability hangs off the same point: after each commit the saved
  events become log lines and metrics (``observe_execution``), and node
  tasks inherit a log context carrying the execution/node correlation ids.

Reliability (Phase 23):

- Retries: a failed attempt the execution's ``RetryPolicy`` allows goes
  back to READY with a backoff; the loop starts it once ``retry_at`` passed.
  Backoff waits are interruptible by cancellation and the deadline.
- Ownership: the loop refreshes the row's heartbeat (a CAS on the revision)
  every ``heartbeat_seconds``. If another writer moved the revision on (an
  out-of-process cancel, a recoverer that declared this run stale) the owner
  learns it within one interval and stops its nodes
  (``ExecutionOwnershipLostError``) instead of running on unseen.
- Recovery: ``recover_stale`` takes over started executions whose owner
  stopped heart-beating (``Execution.recover`` decides the outcome); a
  recovered execution is QUEUED again and ``run`` resumes it where it
  stopped — finished nodes never run again.

Resource limits (Phase 24): the timeouts and attempts an execution was
admitted with are on the aggregate (``ExecutionService`` caps them). This
loop enforces the rest of ``ResourceLimits``: nodes in flight per execution
(``max_concurrency``) and node/execution output size. Output that breaks a
cap never reaches the aggregate: the node fails instead.

Waiting: a node attempt ending in ``NodeWait`` parks the node WAITING. When
nothing runs or can run any more and a node waits, the execution is saved
WAITING and ``run`` returns: no task, heartbeat or worker slot is held while
a person thinks. ``ExecutionService.respond`` (or ``expire_waits``, for wait
deadlines and the execution deadline) queues it again; ``run`` then resumes
it like a recovered execution, and the answered node's next attempt gets the
response as ``context.resume``.

Workers (``akasha.workers``) call ``run``, ``recover_stale`` and
``expire_waits``; the queue never reaches in here. Control flow is generic
(``ExecutionPlan.readiness``): the loop never inspects node types.
"""

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import partial
from types import MappingProxyType

from akasha.application.events import EventPublisher
from akasha.application.executions import load_execution, save_execution
from akasha.application.persistence import ConcurrentModificationError, UnitOfWork
from akasha.application.workflows import UowFactory
from akasha.domain.execution import (
    Execution,
    ExecutionError,
    ExecutionStatus,
    NodeExecutionStatus,
    WaitState,
)
from akasha.domain.limits import ResourceLimits, json_size
from akasha.domain.node import (
    NodeErrorKind,
    NodeEvents,
    NodeExecutionContext,
    NodeMetrics,
    NodeResult,
    NodeWait,
)
from akasha.domain.primitives import utcnow
from akasha.errors import AkashaError, ConfigurationError, InvalidStateTransitionError
from akasha.observability.execution import observe_execution
from akasha.observability.logging import log_context
from akasha.observability.metrics import EngineMetrics
from akasha.runtime.credentials import NodeCredentials, SecretSource
from akasha.runtime.executor import NodeOutcome, execute_node
from akasha.runtime.node_logs import NodeLogCapture, capturing
from akasha.runtime.plan import (
    ExecutionGraphInvalidError,
    ExecutionPlan,
    NodeCatalog,
    load_plan,
)

Clock = Callable[[], datetime]

_DECIDED = (NodeExecutionStatus.COMPLETED, NodeExecutionStatus.SKIPPED)


logger = logging.getLogger("akasha.runtime")


class ExecutionAlreadyRunningError(AkashaError):
    code = "EXECUTION_ALREADY_RUNNING"
    http_status = 409


class ExecutionOwnershipLostError(ConcurrentModificationError):
    """Another writer took the running execution over (cancelled it out of
    process, or recovered it as stale); this runtime stopped its nodes."""

    code = "EXECUTION_OWNERSHIP_LOST"


class StateManager:
    """Loads and persists the execution aggregate; one short transaction per
    call. Never held open across node execution (backend/AGENTS.md §16).
    Observes and publishes the saved transitions' events once the save
    committed."""

    def __init__(
        self, uow: UowFactory, events: EventPublisher, metrics: EngineMetrics
    ) -> None:
        self._uow = uow
        self._events = events
        self._metrics = metrics

    async def load(self, execution_id: str) -> Execution:
        async with self._uow() as uow:
            return await load_execution(uow, execution_id)

    async def save(self, execution: Execution) -> None:
        async with self._uow() as uow:
            await save_execution(uow, execution)
            await uow.commit()
        await self._committed(execution)

    async def heartbeat(self, execution: Execution) -> None:
        async with self._uow() as uow:
            owned = await uow.executions.heartbeat(
                execution.id, revision=execution.revision
            )
            await uow.commit()
        if not owned:
            raise ExecutionOwnershipLostError(
                "The execution was taken over by another writer.",
                details={"execution_id": execution.id},
            )

    async def list_stale(self, *, stale_after: timedelta, limit: int) -> list[str]:
        async with self._uow() as uow:
            return list(
                await uow.executions.list_stale(stale_after=stale_after, limit=limit)
            )

    async def list_due_waits(self, *, now: datetime, limit: int) -> list[str]:
        async with self._uow() as uow:
            return list(await uow.executions.list_due_waits(now=now, limit=limit))

    async def recover(
        self,
        execution_id: str,
        *,
        claim: Callable[[UnitOfWork], Awaitable[bool]],
        settle: Callable[[Execution], None],
    ) -> Execution | None:
        """Claim, settle and save one execution in one transaction; None
        when ``claim`` refuses (it no longer qualifies, or another worker
        holds it). Used for stale runs and for due waits."""
        async with self._uow() as uow:
            if not await claim(uow):
                return None
            execution = await load_execution(uow, execution_id)
            settle(execution)
            await save_execution(uow, execution)
            await uow.commit()
        await self._committed(execution)
        return execution

    async def _committed(self, execution: Execution) -> None:
        execution.pull_logs()  # stored; nothing consumes them in process
        events = execution.pull_events()
        observe_execution(execution, events, self._metrics)
        await self._events.publish(events)


@dataclass
class _CancelRequest:
    event: asyncio.Event = field(default_factory=asyncio.Event)
    reason: str = ""
    # Set when the runtime stops the run for any reason (cancel, timeout,
    # failure): what ``NodeExecutionContext.is_cancelled`` reports.
    stopping: bool = False
    # Serialized size of the outputs completed nodes produced so far; checked
    # against ResourceLimits.max_execution_output_bytes.
    output_bytes: int = 0

    def should_stop(self) -> bool:
        return self.stopping or self.event.is_set()


async def _claim_stale(
    execution_id: str, stale_after: timedelta, uow: UnitOfWork
) -> bool:
    return await uow.executions.claim_stale(execution_id, stale_after=stale_after)


async def _claim_due_wait(execution_id: str, now: datetime, uow: UnitOfWork) -> bool:
    return await uow.executions.claim_due_wait(execution_id, now=now)


def _expire(execution: Execution, *, now: datetime) -> None:
    execution.expire_waits(now=now)


def _verdict(
    request: _CancelRequest,
    deadline: float | None,
    now: float,
    failure: ExecutionError | None,
) -> str | None:
    """How the run must end, if it must. Precedence when several apply at
    once: the user's cancellation, then the deadline, then a node failure."""
    if request.event.is_set():
        return "cancel"
    if deadline is not None and now >= deadline:
        return "time_out"
    if failure is not None:
        return "fail"
    return None


@dataclass(frozen=True)
class _Attempt:
    node_id: str  # node instance id
    node_type: str
    context: NodeExecutionContext
    logs: NodeLogCapture


_Running = dict[asyncio.Task[NodeOutcome], _Attempt]


class ExecutionRuntime:
    """Executes queued executions of published versions to a terminal state.

    Usable from tests, the CLI and workers; no HTTP dependency. One
    instance may run many executions concurrently on one event loop.
    """

    def __init__(
        self,
        uow: UowFactory,
        catalog: NodeCatalog,
        *,
        max_concurrency: int = 8,
        clock: Clock = utcnow,
        events: EventPublisher | None = None,
        metrics: EngineMetrics | None = None,
        credentials: SecretSource | None = None,
        heartbeat_seconds: float = 5.0,
        limits: ResourceLimits | None = None,
        plugins: Mapping[str, str] = MappingProxyType({}),
    ) -> None:
        """``plugins``: enabled plugin id → version (``PluginRegistry.versions``),
        checked against each version's pinned plugin versions."""
        if max_concurrency < 1:
            raise ConfigurationError("max_concurrency must be >= 1")
        if heartbeat_seconds <= 0:
            raise ConfigurationError("heartbeat_seconds must be > 0")
        self._heartbeat = heartbeat_seconds
        self._limits = limits or ResourceLimits()
        self._metrics = metrics or EngineMetrics()
        self._state = StateManager(uow, events or EventPublisher(), self._metrics)
        self._catalog = catalog
        self._plugins = plugins
        self._max_concurrency = max_concurrency
        self._clock = clock
        self._active: dict[str, _CancelRequest] = {}
        self._credentials = credentials

    async def run(self, execution_id: str) -> Execution:
        """Run a QUEUED execution to a terminal state and return it; a
        recovered execution resumes where it stopped.

        If the run itself is cancelled (e.g. process shutdown), node tasks
        are stopped and the execution is deliberately left RUNNING in the
        database: a crash is not a clean failure (architecture §139). Its
        heartbeat stops, so ``recover_stale`` takes it over later.
        """
        if execution_id in self._active:
            raise ExecutionAlreadyRunningError(
                "The execution is already running.",
                details={"execution_id": execution_id},
            )
        request = self._active[execution_id] = _CancelRequest()
        self._metrics.active_executions.inc()
        try:
            with log_context(execution_id=execution_id):
                return await self._run(execution_id, request)
        finally:
            self._metrics.active_executions.dec()
            del self._active[execution_id]

    async def cancel(self, execution_id: str, *, reason: str = "") -> None:
        """Request cancellation.

        Running here: signal the scheduler loop, which stops in-flight nodes
        cooperatively and records CANCELLED (await ``run`` for the result).
        Not running here: cancel the persisted execution directly; the CAS
        save makes a concurrently starting owner lose (or this call lose
        with ``ConcurrentModificationError``). Terminal executions raise
        ``InvalidStateTransitionError``.
        """
        request = self._active.get(execution_id)
        if request is not None:
            if not request.event.is_set():  # first request wins
                request.reason = reason
                request.event.set()
            return
        execution = await self._state.load(execution_id)
        execution.cancel(reason=reason, now=self._clock())
        await self._state.save(execution)

    async def recover_stale(
        self, *, stale_after: timedelta, limit: int = 100
    ) -> list[Execution]:
        """Take over started executions without an owner heartbeat for
        ``stale_after`` (their process died or hung) and settle each with
        ``Execution.recover``. Returns them: those now QUEUED must be
        dispatched again, the rest ended (failed, cancelled, timed out).

        Safe to run from every worker at once: each execution is claimed
        under a row lock that skips rows another recoverer holds, and a
        claimed execution is no longer stale once saved."""
        ids = await self._state.list_stale(stale_after=stale_after, limit=limit)
        recovered: list[Execution] = []
        for execution_id in ids:
            if execution_id in self._active:
                continue  # ours and alive; only its heartbeat is late
            try:
                execution = await self._state.recover(
                    execution_id,
                    claim=partial(_claim_stale, execution_id, stale_after),
                    settle=self._settle,
                )
            except AkashaError as exc:
                # One unrecoverable row (e.g. its version's nodes changed
                # shape) must not block the others; it stays stale and is
                # retried on the next pass, loudly.
                logger.error(
                    "execution_recovery_failed",
                    extra={
                        "event": "execution_recovery_failed",
                        "execution_id": execution_id,
                        "reason": exc.code,
                    },
                )
                continue
            if execution is not None:
                recovered.append(execution)
        return recovered

    async def expire_waits(self, *, limit: int = 100) -> list[Execution]:
        """Settle WAITING executions that are due: a node's wait deadline
        or the execution deadline passed (``Execution.expire_waits``).
        Returns them: those now QUEUED must be dispatched, the rest ended.
        Safe from every worker at once, like ``recover_stale``."""
        now = self._clock()
        settled: list[Execution] = []
        for execution_id in await self._state.list_due_waits(now=now, limit=limit):
            execution = await self._state.recover(
                execution_id,
                claim=partial(_claim_due_wait, execution_id, now),
                settle=partial(_expire, now=now),
            )
            if execution is not None:
                settled.append(execution)
        return settled

    def _settle(self, execution: Execution) -> None:
        graph = execution.workflow_version.graph
        repeatable = frozenset(
            node_id
            for node_id, instance in graph.nodes.items()
            if (impl := self._catalog.get(instance.node_type)) is not None
            and not impl.definition.capabilities.has_side_effects
        )
        execution.recover(repeatable=repeatable, now=self._clock())

    # -- internals ----------------------------------------------------------

    async def _run(self, execution_id: str, request: _CancelRequest) -> Execution:
        execution = await self._state.load(execution_id)
        if execution.status is not ExecutionStatus.QUEUED:
            raise InvalidStateTransitionError(
                f"only queued executions can be run, not {execution.status}",
                details={"action": "run", "from": str(execution.status)},
            )
        if request.event.is_set():  # cancelled before it started
            execution.cancel(reason=request.reason, now=self._clock())
            await self._state.save(execution)
            return execution
        try:
            plan = load_plan(execution.workflow_version, self._catalog, self._plugins)
        except ExecutionGraphInvalidError as exc:
            codes = sorted({i.code.value for i in exc.result.issues})
            execution.fail(
                ExecutionError(
                    code=exc.code,
                    message=exc.message,
                    kind=NodeErrorKind.VALIDATION,
                    details={"issue_codes": ",".join(codes)},
                ),
                now=self._clock(),
            )
            await self._state.save(execution)
            return execution

        now = self._clock()
        elapsed = 0.0
        if execution.is_resumed:
            if plan.node_versions != dict(execution.metadata.node_versions):
                # Finished nodes ran on the recorded versions; mixing in
                # others would make the result unreproducible (AGENTS §73).
                execution.fail(
                    ExecutionError(
                        code="NODE_VERSIONS_CHANGED",
                        message="node versions changed since the execution "
                        "started; it cannot be resumed",
                        kind=NodeErrorKind.CONFIGURATION,
                    ),
                    now=now,
                )
                await self._state.save(execution)
                return execution
            started = execution.started_at or now  # set: is_resumed
            elapsed = max(0.0, (now - started).total_seconds())
            request.output_bytes = sum(
                json_size(ne.outputs)
                for ne in execution.node_executions.values()
                if ne.status is NodeExecutionStatus.COMPLETED
            )
        execution.start(now=now, node_versions=plan.node_versions)
        # Fresh: marks the roots READY. Resumed: re-derives readiness from
        # what finished, so no persisted progress can leave a node stranded.
        for node_id in plan.roots:
            if execution.node(node_id).status is NodeExecutionStatus.PENDING:
                execution.mark_node_ready(node_id)
        for node_id, ne in sorted(execution.node_executions.items()):
            if ne.status in _DECIDED:
                self._advance(execution, plan, node_id)
        await self._state.save(execution)
        await self._schedule(execution, plan, request, elapsed=elapsed)
        return execution

    async def _schedule(
        self,
        execution: Execution,
        plan: ExecutionPlan,
        request: _CancelRequest,
        *,
        elapsed: float,
    ) -> None:
        """``elapsed``: seconds the execution already ran before this
        process took it (resume); the deadline counts from the first start."""
        loop = asyncio.get_running_loop()
        limit = execution.timeout_policy.execution_timeout
        deadline = (
            None if limit is None else loop.time() + limit.total_seconds() - elapsed
        )
        next_beat = loop.time() + self._heartbeat
        running: _Running = {}
        failure: ExecutionError | None = None
        dirty = False  # recorded outcomes not yet persisted
        cancel_signal = asyncio.create_task(request.event.wait())
        try:
            while True:
                # Checked before launching, so no new node starts once the
                # run was cancelled, timed out or has failed.
                verdict = _verdict(request, deadline, loop.time(), failure)
                if verdict is not None:
                    await self._end(execution, running, request, verdict, failure)
                    return
                if loop.time() >= next_beat:
                    await self._state.heartbeat(execution)
                    next_beat = loop.time() + self._heartbeat
                if not await self._launch(execution, plan, running, request) and dirty:
                    await self._state.save(execution)
                backoff = self._retry_wait(execution)
                if not running and backoff is None:
                    # _launch and _retry_wait read the clock separately: a
                    # backoff that ended in between leaves a READY node that
                    # neither launched nor waits. Launch it, never finish.
                    statuses = {ne.status for ne in execution.node_executions.values()}
                    if NodeExecutionStatus.READY in statuses:
                        continue
                    if NodeExecutionStatus.WAITING in statuses:
                        # Nothing else can run: park it, release the worker.
                        execution.wait(now=self._clock())
                        await self._state.save(execution)
                        return
                    break
                waits = [next_beat - loop.time()]
                if backoff is not None:
                    waits.append(backoff)
                if deadline is not None:
                    waits.append(deadline - loop.time())
                done, _ = await asyncio.wait(
                    [*running, cancel_signal],
                    timeout=max(0.0, min(waits)),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                finished = [t for t in running if t in done]
                completed, failure = self._record(
                    execution, running, finished, request, retry=True
                )
                dirty = bool(finished)
                if failure is None:
                    for completed_id in completed:
                        self._advance(execution, plan, completed_id)
            execution.complete(now=self._clock())
            await self._state.save(execution)
        finally:
            # Whatever ended the loop (including a persistence error or the
            # run being cancelled), no node task may outlive it.
            cancel_signal.cancel()
            if running:
                request.stopping = True
                for task in running:
                    task.cancel()
                await asyncio.wait(running)

    async def _end(
        self,
        execution: Execution,
        running: _Running,
        request: _CancelRequest,
        verdict: str,
        failure: ExecutionError | None,
    ) -> None:
        if verdict == "cancel":
            execution.request_cancellation(reason=request.reason, now=self._clock())
        request.stopping = True
        await self._stop(execution, running, request)
        now = self._clock()
        if verdict == "cancel":
            execution.cancel(now=now)
            await self._state.save(execution)
        elif verdict == "time_out":
            execution.time_out(now=now)
            await self._state.save(execution)
        elif failure is not None:
            execution.fail(failure, now=now)
            await self._state.save(execution)

    def _retry_wait(self, execution: Execution) -> float | None:
        """Seconds until the next READY node's retry backoff ends; None when
        no node waits for one (READY nodes without a backoff wait for a
        concurrency slot, i.e. for a running node, not for time)."""
        now = self._clock()
        due = [
            ne.retry_at
            for ne in execution.node_executions.values()
            if ne.status is NodeExecutionStatus.READY
            and ne.retry_at is not None
            and ne.retry_at > now
        ]
        return None if not due else (min(due) - now).total_seconds()

    async def _launch(
        self,
        execution: Execution,
        plan: ExecutionPlan,
        running: _Running,
        request: _CancelRequest,
    ) -> bool:
        """Scheduler: start READY nodes (id order) up to the concurrency
        limit. RUNNING is persisted before any node code can run. Returns
        whether it persisted (i.e. launched anything)."""
        slots = self._max_concurrency - len(running)
        now = self._clock()
        batch = sorted(
            node_id
            for node_id, ne in execution.node_executions.items()
            if ne.status is NodeExecutionStatus.READY
            and (ne.retry_at is None or ne.retry_at <= now)
        )[:slots]
        if not batch:
            return False
        for node_id in batch:
            execution.start_node(node_id, now=now)
        await self._state.save(execution)
        version = execution.workflow_version
        for node_id in batch:
            ne = execution.node(node_id)
            instance = plan.graph.nodes[node_id]
            definition = plan.implementations[node_id].definition
            context = NodeExecutionContext(
                execution_id=execution.id,
                node_execution_id=ne.id,
                node_instance_id=node_id,
                workflow_id=execution.workflow_id,
                workflow_version=str(version.version),
                # A copy: node code must not be able to mutate the snapshot.
                config=deepcopy(instance.config),
                # Scoped to references in this node's snapshot config.
                credentials=None
                if self._credentials is None
                else NodeCredentials(self._credentials, instance.config),
                inputs=plan.inputs_for(node_id, execution),
                logger=logging.getLogger(f"akasha.nodes.{instance.node_type}"),
                is_cancelled=request.should_stop,
                runtime_metadata={
                    "workflow_version_id": version.id,
                    "engine_version": execution.metadata.engine_version,
                    "attempt": ne.attempt,
                },
                # A copy for the same reason as ``config``.
                execution_input=deepcopy(execution.input),
                resume=deepcopy(ne.resume_input),
                metrics=NodeMetrics(definition.metrics),
                events=NodeEvents(definition.events),
            )
            # The task copies the context it is created in: every log line
            # of the node's code (and libraries it calls) carries these ids,
            # and its ``context.logger`` lines reach this attempt's capture.
            logs = NodeLogCapture(self._clock)
            with (
                capturing(logs),
                log_context(
                    execution_id=execution.id,
                    workflow_id=execution.workflow_id,
                    correlation_id=execution.metadata.correlation_id,
                    node_id=node_id,
                    node_execution_id=ne.id,
                    node_type=instance.node_type,
                ),
            ):
                task = asyncio.create_task(
                    execute_node(
                        plan.implementations[node_id],
                        context,
                        limit=execution.timeout_policy.node_timeout,
                    )
                )
            running[task] = _Attempt(node_id, instance.node_type, context, logs)
        return True

    def _advance(self, execution: Execution, plan: ExecutionPlan, done: str) -> None:
        """DependencyResolver step after ``done`` completed: mark decided
        downstream nodes READY or SKIPPED. A skip decides that node's own
        downstream too, so skips cascade (breadth-first, id order)."""
        queue = deque([done])
        while queue:
            for node_id in plan.downstream[queue.popleft()]:
                if execution.node(node_id).status is not NodeExecutionStatus.PENDING:
                    continue
                decision = plan.readiness(node_id, execution)
                if decision is NodeExecutionStatus.READY:
                    execution.mark_node_ready(node_id)
                elif decision is NodeExecutionStatus.SKIPPED:
                    execution.skip_node(node_id, now=self._clock())
                    queue.append(node_id)

    def _record(
        self,
        execution: Execution,
        running: _Running,
        finished: list[asyncio.Task[NodeOutcome]],
        request: _CancelRequest,
        *,
        retry: bool,
    ) -> tuple[list[str], ExecutionError | None]:
        """Record finished attempts in node-id order (deterministic when
        several finish together); returns the completed node ids and the
        first failure. With ``retry``, a failed attempt the retry policy
        allows is scheduled again instead of failing the node."""
        completed: list[str] = []
        first_failure: ExecutionError | None = None
        finished.sort(key=lambda t: running[t].node_id)
        for task in finished:
            attempt = running.pop(task)
            node_id = attempt.node_id
            outcome = self._limit_output(task.result(), node_id, attempt, request)
            now = self._clock()
            self._flush(execution, attempt, now)
            if isinstance(outcome, NodeResult):
                execution.complete_node(node_id, outputs=outcome.outputs, now=now)
                completed.append(node_id)
            elif isinstance(outcome, NodeWait):
                deadline = None if outcome.timeout is None else now + outcome.timeout
                execution.wait_node(
                    node_id,
                    WaitState(
                        outcome.prompt,
                        outcome.response_schema.json_schema,
                        deadline,
                    ),
                    now=now,
                )
            elif retry and execution.can_retry_node(node_id, outcome):
                execution.retry_node(node_id, outcome, now=now)
            else:
                execution.fail_node(node_id, outcome, now=now)
                first_failure = first_failure or outcome
        return completed, first_failure

    def _limit_output(
        self,
        outcome: NodeOutcome,
        node_id: str,
        attempt: _Attempt,
        request: _CancelRequest,
    ) -> NodeOutcome:
        """The outcome, or the output-limit failure that replaces it."""
        if not isinstance(outcome, NodeResult):
            return outcome
        size = json_size(dict(outcome.outputs))
        exceeded = self._limits.check_output(node_id, size, request.output_bytes)
        if exceeded is None:
            request.output_bytes += size
            return outcome
        limit, error = exceeded
        self._metrics.resource_limit_exceeded.labels(limit.value).inc()
        logger.warning(
            "node_output_limit_exceeded",
            extra={
                "event": "node_output_limit_exceeded",
                "node_id": node_id,
                "node_type": attempt.node_type,
                "limit": limit.value,
                "size": size,
            },
        )
        return error

    def _flush(self, execution: Execution, attempt: _Attempt, now: datetime) -> None:
        """Record what the node emitted through ``context.events`` /
        ``context.metrics`` / ``context.logger`` — also for failed and
        cancelled attempts: the node really did it.

        Once per attempt by design: node tasks never touch the aggregate or
        the database (module docstring), and every emitted event lands in
        the same save as the attempt's outcome. Live progress would need a
        channel from node tasks to this loop plus a save per emission;
        revisit when a consumer of live progress (a live-update gateway)
        exists.
        """
        for name in attempt.context.events.emitted:
            execution.record_node_event(attempt.node_id, name, now=now)
        execution.record_node_logs(attempt.node_id, attempt.logs.lines())
        for name, amount in attempt.context.metrics.totals.items():
            self._metrics.node_metric.labels(attempt.node_type, name).inc(amount)

    async def _stop(
        self, execution: Execution, running: _Running, request: _CancelRequest
    ) -> None:
        """Cancel in-flight attempts and wait until each really ended.

        Cancellation reaches node code as ``CancelledError`` at its next
        await, so ``finally`` cleanup runs. Attempts that finished anyway
        are recorded truthfully; the rest end CANCELLED via the terminal
        cascade. A node that swallows cancellation blocks here — the runtime
        never reports a terminal state while node code is still running.
        """
        if not running:
            return
        for task in running:
            task.cancel()
        await asyncio.wait(running)
        # The run is ending: nothing is retried any more.
        self._record(
            execution,
            running,
            [t for t in running if not t.cancelled()],
            request,
            retry=False,
        )
        now = self._clock()
        for attempt in running.values():  # the cancelled rest
            self._flush(execution, attempt, now)
        running.clear()
