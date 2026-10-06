"""Workers (roadmap Phase 22, architecture §71/§140).

    API -> ExecutionService.create_execution -> ExecutionQueue -> Worker
        -> ExecutionRuntime.run

A ``Worker`` only moves jobs to the ``ExecutionRuntime``; execution
semantics (state machine, scheduling, cancellation, persistence) stay in the
runtime. The ``WorkerManager`` owns a worker's task and the recovery pass (queue
recovery, and stale-execution recovery through the runtime).

Delivery is at-least-once, and duplicates are harmless by construction: the
runtime runs only QUEUED executions and every save is compare-and-swap, so a
second delivery of the same execution is refused (``job_skipped``) and never
runs node code again.

Lifecycle:
- start: register (heartbeat), then take jobs, at most ``concurrency`` runs.
- heartbeat: every ``heartbeat_seconds``; the registration expires after
  three missed beats, and ``recover`` then hands the dead worker's held jobs
  back to the queue.
- graceful stop: take no new jobs, let runs finish for ``shutdown_grace``
  seconds, then cancel the rest through ``ExecutionRuntime.cancel`` (they
  end CANCELLED — never silently RUNNING), acknowledge, deregister.
- crash (task cancelled / process killed): nothing is acknowledged. Held
  jobs are redelivered after the registration expired (and skipped: the
  execution is not QUEUED). An execution the dead worker had started stops
  heart-beating; once stale, ``ExecutionRuntime.recover_stale`` settles it
  (§139: a crash is not a clean failure, the recovery rules decide) and the
  manager dispatches it again to be resumed.
"""

import asyncio
import contextlib
import logging
import os
import socket
from collections.abc import Callable
from datetime import datetime, timedelta

from akasha.application.executions import ExecutionNotFoundError, ExecutionService
from akasha.application.persistence import ConcurrentModificationError
from akasha.application.queue import (
    ExecutionQueue,
    Job,
    QueueUnavailableError,
    WorkerInfo,
    WorkerState,
)
from akasha.domain.execution import ExecutionStatus
from akasha.domain.primitives import new_id, utcnow
from akasha.errors import AkashaError, ConfigurationError, InvalidStateTransitionError
from akasha.observability.logging import log_context
from akasha.observability.metrics import EngineMetrics
from akasha.runtime.runtime import ExecutionAlreadyRunningError, ExecutionRuntime

logger = logging.getLogger("akasha.workers")

Clock = Callable[[], datetime]

# The job is stale, not the worker broken: the execution is not QUEUED any
# more (run by another delivery, cancelled), gone, or owned by a concurrent
# writer.
_SKIP = (
    InvalidStateTransitionError,
    ExecutionNotFoundError,
    ExecutionAlreadyRunningError,
    ConcurrentModificationError,
)


class Worker:
    def __init__(
        self,
        queue: ExecutionQueue,
        runtime: ExecutionRuntime,
        *,
        worker_id: str | None = None,
        concurrency: int = 4,
        poll_seconds: float = 1.0,
        heartbeat_seconds: float = 5.0,
        shutdown_grace_seconds: float = 30.0,
        clock: Clock = utcnow,
    ) -> None:
        if concurrency < 1 or poll_seconds <= 0 or heartbeat_seconds <= 0:
            raise ConfigurationError(
                "worker concurrency, poll and heartbeat intervals must be positive"
            )
        self.worker_id = worker_id or new_id("wrk")
        self._queue = queue
        self.runtime = runtime
        self._concurrency = concurrency
        self._poll = poll_seconds
        self._heartbeat = heartbeat_seconds
        self._grace = max(0.0, shutdown_grace_seconds)
        self._clock = clock
        self._started_at = clock()
        self._stop = asyncio.Event()
        self._active: dict[asyncio.Task[None], Job] = {}

    @property
    def heartbeat_ttl(self) -> float:
        return 3 * self._heartbeat

    def info(self) -> WorkerInfo:
        return WorkerInfo(
            worker_id=self.worker_id,
            hostname=socket.gethostname(),
            pid=os.getpid(),
            state=WorkerState.STOPPING if self._stop.is_set() else WorkerState.RUNNING,
            concurrency=self._concurrency,
            started_at=self._started_at,
            heartbeat_at=self._clock(),
            active_executions=tuple(
                sorted(j.execution_id for j in self._active.values())
            ),
        )

    async def beat(self) -> None:
        """Publish the registration once; a failure is logged, not raised —
        a missed beat is what expiry is for."""
        try:
            await self._queue.heartbeat(self.info(), ttl=self.heartbeat_ttl)
        except QueueUnavailableError:
            logger.warning(
                "worker_heartbeat_failed",
                extra={"event": "worker_heartbeat_failed", "worker_id": self.worker_id},
            )

    def request_stop(self) -> None:
        """Begin a graceful stop; ``run`` returns once it completed."""
        self._stop.set()

    async def run(self) -> None:
        with log_context(worker_id=self.worker_id):
            await self.beat()
            heart = asyncio.create_task(self._beat_forever(), name="worker-heartbeat")
            logger.info("worker_started", extra={"event": "worker_started"})
            try:
                await self._take_jobs()
                await self.beat()  # announce STOPPING
                await self._drain()
                returned = await self._queue.deregister(self.worker_id)
            finally:
                heart.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heart
                # Only when ``run`` itself was cancelled (a crash, not a
                # stop): no run outlives its worker. Jobs stay unacked.
                for task in self._active:
                    task.cancel()
                if self._active:
                    await asyncio.wait(self._active)
            logger.info(
                "worker_stopped",
                extra={"event": "worker_stopped", "jobs_returned": returned},
            )

    async def _beat_forever(self) -> None:
        while True:
            await asyncio.sleep(self._heartbeat)
            await self.beat()

    async def _take_jobs(self) -> None:
        stop = asyncio.create_task(self._stop.wait())
        try:
            while not self._stop.is_set():
                if len(self._active) >= self._concurrency:
                    await asyncio.wait(
                        [*self._active, stop], return_when=asyncio.FIRST_COMPLETED
                    )
                    continue
                try:
                    job = await self._queue.dequeue(
                        self.worker_id, block_seconds=self._poll
                    )
                except QueueUnavailableError:
                    logger.warning(
                        "worker_dequeue_failed",
                        extra={"event": "worker_dequeue_failed"},
                    )
                    # Back off one poll interval; a stop request cuts it short.
                    await asyncio.wait([stop], timeout=self._poll)
                    continue
                if job is None:
                    continue
                if self._stop.is_set():
                    break  # still held: ``deregister`` hands it back
                task = asyncio.create_task(self._process(job))
                self._active[task] = job
                task.add_done_callback(self._forget)
        finally:
            stop.cancel()

    def _forget(self, task: asyncio.Task[None]) -> None:
        self._active.pop(task, None)

    async def _process(self, job: Job) -> None:
        with log_context(execution_id=job.execution_id):
            try:
                execution = await self.runtime.run(job.execution_id)
            except _SKIP as exc:
                logger.info(
                    "job_skipped", extra={"event": "job_skipped", "reason": exc.code}
                )
            except Exception:
                # Logged with its traceback, then acknowledged: the database
                # is the truth. An execution that never started is still
                # QUEUED and ``dispatch_queued`` re-dispatches it; one that
                # started stops heart-beating and is recovered as stale.
                logger.exception("job_failed", extra={"event": "job_failed"})
            else:
                logger.info(
                    "job_completed",
                    extra={"event": "job_completed", "status": str(execution.status)},
                )
            try:
                await self._queue.ack(self.worker_id, job)
            except QueueUnavailableError:
                # Still held: deregistration or expiry redelivers it, and
                # the redelivery is skipped (the execution is not QUEUED).
                logger.warning("job_ack_failed", extra={"event": "job_ack_failed"})

    async def _drain(self) -> None:
        if not self._active:
            return
        _, overdue = await asyncio.wait(list(self._active), timeout=self._grace)
        for task in overdue:
            execution_id = self._active[task].execution_id
            try:
                await self.runtime.cancel(execution_id, reason="worker shutdown")
            except AkashaError as exc:
                # It reached a terminal state meanwhile: nothing to cancel.
                logger.info(
                    "worker_shutdown_cancel_skipped",
                    extra={
                        "event": "worker_shutdown_cancel_skipped",
                        "execution_id": execution_id,
                        "reason": exc.code,
                    },
                )
        if overdue:
            await asyncio.wait(overdue)


class WorkerManager:
    """Owns one worker's task and the periodic recovery pass. Either may run
    in the API process or in a dedicated ``akasha worker`` process."""

    def __init__(
        self,
        worker: Worker,
        queue: ExecutionQueue,
        executions: ExecutionService,
        *,
        metrics: EngineMetrics | None = None,
        recovery_seconds: float = 30.0,
        stale_seconds: float = 60.0,
    ) -> None:
        if recovery_seconds <= 0 or stale_seconds <= 0:
            raise ConfigurationError("recovery_seconds and stale_seconds must be > 0")
        self._stale = timedelta(seconds=stale_seconds)
        self.worker = worker
        self._queue = queue
        self._executions = executions
        self._metrics = metrics or EngineMetrics()
        self._recovery = recovery_seconds
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        """Register the worker, then run it and the recovery pass in the
        background."""
        if self._tasks:
            return
        await self.worker.beat()
        self._tasks = [
            asyncio.create_task(self.worker.run(), name="worker"),
            asyncio.create_task(self._recover_forever(), name="worker-recovery"),
        ]
        self._tasks[0].add_done_callback(_report_crash)

    async def stop(self) -> None:
        if not self._tasks:
            return
        worker, recovery = self._tasks
        self._tasks = []
        recovery.cancel()
        self.worker.request_stop()
        with contextlib.suppress(asyncio.CancelledError):
            await recovery
        # A crashed worker was already reported by ``_report_crash``.
        with contextlib.suppress(Exception):
            await worker

    async def recover(self) -> tuple[int, int, int]:
        """One recovery pass: take over stale started executions and
        settle due waits, dispatch those queued to resume, redeliver dead
        workers' held jobs,
        re-dispatch executions left QUEUED for ``recovery_seconds``. Returns
        the three counts (stale ones: every execution taken over, whatever
        its outcome)."""
        stale = await self.worker.runtime.recover_stale(stale_after=self._stale)
        # Waits past their deadline continue (timed out) or end; counted
        # with the stale ones, both are "taken over and settled".
        stale += await self.worker.runtime.expire_waits()
        for execution in stale:
            if execution.status is ExecutionStatus.QUEUED:
                # A lost job is caught by ``dispatch_queued`` later.
                with contextlib.suppress(QueueUnavailableError):
                    await self._queue.enqueue(Job(execution.id))
        redelivered = await self._queue.recover()
        redispatched = await self._executions.dispatch_queued(
            older_than=timedelta(seconds=self._recovery)
        )
        self._metrics.queue_depth.set(await self._queue.depth())
        if redelivered or redispatched or stale:
            logger.warning(
                "queue_recovered",
                extra={
                    "event": "queue_recovered",
                    "jobs_redelivered": redelivered,
                    "executions_redispatched": redispatched,
                    "stale_executions_recovered": len(stale),
                },
            )
        return redelivered, redispatched, len(stale)

    async def workers(self) -> list[WorkerInfo]:
        return await self._queue.workers()

    async def _recover_forever(self) -> None:
        while True:
            try:
                await self.recover()
            except Exception:
                # Queue or database down: the next pass retries (§99).
                logger.exception(
                    "queue_recovery_failed", extra={"event": "queue_recovery_failed"}
                )
            await asyncio.sleep(self._recovery)


def _report_crash(task: asyncio.Task[None]) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.error(
            "worker_crashed",
            extra={"event": "worker_crashed"},
            exc_info=task.exception(),
        )
