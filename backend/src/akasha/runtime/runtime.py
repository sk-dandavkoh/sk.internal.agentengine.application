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

Scope: no retries (Phase 23), no queue/worker/leases (Phase 22), no
recovery of executions left RUNNING by a dead process (Phase 23). Control
flow is generic (``ExecutionPlan.readiness``): the loop never inspects node
types.
"""

import asyncio
import logging
from collections import deque
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime

from akasha.application.events import EventPublisher
from akasha.application.executions import load_execution, save_execution
from akasha.application.projects import UowFactory
from akasha.domain.execution import (
    Execution,
    ExecutionError,
    ExecutionStatus,
    NodeExecutionStatus,
)
from akasha.domain.node import NodeErrorKind, NodeExecutionContext, NodeResult
from akasha.domain.primitives import utcnow
from akasha.errors import AkashaError, ConfigurationError, InvalidStateTransitionError
from akasha.observability.execution import observe_execution
from akasha.observability.logging import log_context
from akasha.observability.metrics import EngineMetrics
from akasha.runtime.executor import NodeOutcome, execute_node
from akasha.runtime.plan import (
    ExecutionGraphInvalidError,
    ExecutionPlan,
    NodeCatalog,
    load_plan,
)

Clock = Callable[[], datetime]


class ExecutionAlreadyRunningError(AkashaError):
    code = "EXECUTION_ALREADY_RUNNING"
    http_status = 409


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

    def should_stop(self) -> bool:
        return self.stopping or self.event.is_set()


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


_Running = dict[asyncio.Task[NodeOutcome], str]  # task -> node instance id


class ExecutionRuntime:
    """Executes queued executions of published versions to a terminal state.

    Usable from tests, a CLI or (later) a worker; no HTTP dependency. One
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
    ) -> None:
        if max_concurrency < 1:
            raise ConfigurationError("max_concurrency must be >= 1")
        self._metrics = metrics or EngineMetrics()
        self._state = StateManager(uow, events or EventPublisher(), self._metrics)
        self._catalog = catalog
        self._max_concurrency = max_concurrency
        self._clock = clock
        self._active: dict[str, _CancelRequest] = {}

    async def run(self, execution_id: str) -> Execution:
        """Run a QUEUED execution to a terminal state and return it.

        If the run itself is cancelled (e.g. process shutdown), node tasks
        are stopped and the execution is deliberately left RUNNING in the
        database: a crash is not a clean failure (architecture §139);
        recovery is Phase 23.
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

    # -- internals ----------------------------------------------------------

    async def _run(self, execution_id: str, request: _CancelRequest) -> Execution:
        execution = await self._state.load(execution_id)
        if execution.status is not ExecutionStatus.QUEUED:
            raise InvalidStateTransitionError(
                f"only queued executions can be run, not {execution.status}",
                details={"action": "run", "from": str(execution.status)},
            )
        if execution.retry_policy.max_attempts > 1:
            # Refuse rather than silently ignore a configured retry policy.
            raise ConfigurationError(
                "node retries are not supported by this runtime yet",
                details={"execution_id": execution_id},
            )
        if request.event.is_set():  # cancelled before it started
            execution.cancel(reason=request.reason, now=self._clock())
            await self._state.save(execution)
            return execution
        try:
            plan = load_plan(execution.project_version, self._catalog)
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

        execution.start(now=self._clock(), node_versions=plan.node_versions)
        for node_id in plan.roots:
            execution.mark_node_ready(node_id)
        await self._state.save(execution)
        await self._schedule(execution, plan, request)
        return execution

    async def _schedule(
        self, execution: Execution, plan: ExecutionPlan, request: _CancelRequest
    ) -> None:
        loop = asyncio.get_running_loop()
        limit = execution.timeout_policy.execution_timeout
        deadline = None if limit is None else loop.time() + limit.total_seconds()
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
                if not await self._launch(execution, plan, running, request) and dirty:
                    await self._state.save(execution)
                if not running:
                    break
                timeout = None if deadline is None else max(0.0, deadline - loop.time())
                done, _ = await asyncio.wait(
                    [*running, cancel_signal],
                    timeout=timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                finished = [t for t in running if t in done]
                completed, failure = self._record(execution, running, finished)
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
        await self._stop(execution, running)
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
        batch = sorted(
            node_id
            for node_id, ne in execution.node_executions.items()
            if ne.status is NodeExecutionStatus.READY
        )[:slots]
        if not batch:
            return False
        now = self._clock()
        for node_id in batch:
            execution.start_node(node_id, now=now)
        await self._state.save(execution)
        version = execution.project_version
        for node_id in batch:
            ne = execution.node(node_id)
            instance = plan.graph.nodes[node_id]
            context = NodeExecutionContext(
                execution_id=execution.id,
                node_execution_id=ne.id,
                node_instance_id=node_id,
                project_id=execution.project_id,
                project_version=str(version.version),
                # A copy: node code must not be able to mutate the snapshot.
                config=deepcopy(instance.config),
                inputs=plan.inputs_for(node_id, execution),
                logger=logging.getLogger(f"akasha.nodes.{instance.node_type}"),
                is_cancelled=request.should_stop,
                runtime_metadata={
                    "project_version_id": version.id,
                    "engine_version": execution.metadata.engine_version,
                    "attempt": ne.attempt,
                },
                # A copy for the same reason as ``config``.
                execution_input=deepcopy(execution.input),
            )
            # The task copies the context it is created in: every log line
            # of the node's code (and libraries it calls) carries these ids.
            with log_context(
                execution_id=execution.id,
                project_id=execution.project_id,
                correlation_id=execution.metadata.correlation_id,
                node_id=node_id,
                node_execution_id=ne.id,
                node_type=instance.node_type,
            ):
                task = asyncio.create_task(
                    execute_node(
                        plan.implementations[node_id],
                        context,
                        limit=execution.timeout_policy.node_timeout,
                    )
                )
            running[task] = node_id
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
    ) -> tuple[list[str], ExecutionError | None]:
        """Record finished attempts in node-id order (deterministic when
        several finish together); returns the completed node ids and the
        first failure."""
        completed: list[str] = []
        first_failure: ExecutionError | None = None
        finished.sort(key=lambda t: running[t])
        for task in finished:
            node_id = running.pop(task)
            outcome = task.result()
            now = self._clock()
            if isinstance(outcome, NodeResult):
                execution.complete_node(node_id, outputs=outcome.outputs, now=now)
                completed.append(node_id)
            else:
                execution.fail_node(node_id, outcome, now=now)
                first_failure = first_failure or outcome
        return completed, first_failure

    async def _stop(self, execution: Execution, running: _Running) -> None:
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
        self._record(execution, running, [t for t in running if not t.cancelled()])
        running.clear()
