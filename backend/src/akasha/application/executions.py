"""Execution use cases (roadmap Phase 08): create and inspect executions.

Creating an execution pins one immutable ProjectVersion and records it as
QUEUED — accepted for execution. *Running* it is the runtime's job
(``akasha.runtime``), done by a worker (Phase 22): after the commit the
execution is dispatched as a ``Job`` on the ``ExecutionQueue``. Every
trigger converges on ``create_execution``.
"""

import logging
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from akasha.application.events import EventPublisher
from akasha.application.persistence import UnitOfWork
from akasha.application.projects import ProjectVersionNotFoundError, UowFactory
from akasha.application.queue import ExecutionQueue, Job, QueueUnavailableError
from akasha.domain.execution import (
    Execution,
    ExecutionMetadata,
    RetryPolicy,
    TimeoutPolicy,
)
from akasha.domain.primitives import utcnow
from akasha.errors import AkashaError
from akasha.observability.execution import observe_execution
from akasha.observability.metrics import EngineMetrics

logger = logging.getLogger("akasha.application.executions")


class ExecutionNotFoundError(AkashaError):
    code = "EXECUTION_NOT_FOUND"
    http_status = 404


async def load_execution(uow: UnitOfWork, execution_id: str) -> Execution:
    execution = await uow.executions.get(execution_id)
    if execution is None:
        raise ExecutionNotFoundError(
            "Execution not found.", details={"execution_id": execution_id}
        )
    return execution


async def save_execution(uow: UnitOfWork, execution: Execution) -> None:
    """Stage the aggregate and its recorded events in ``uow``'s transaction.

    The events stay pending on the aggregate: the caller publishes
    ``execution.pull_events()`` only after ``uow.commit()`` succeeded.
    """
    await uow.executions.save(execution)
    await uow.events.append(execution.pending_events)


class ExecutionService:
    def __init__(
        self,
        uow: UowFactory,
        events: EventPublisher | None = None,
        metrics: EngineMetrics | None = None,
        queue: ExecutionQueue | None = None,
    ) -> None:
        self._uow = uow
        self._events = events or EventPublisher()
        self._metrics = metrics or EngineMetrics()
        # None: nothing dispatches; whoever creates an execution runs it
        # (tests, the CLI's in-process ``project run``).
        self._queue = queue

    async def create_execution(
        self,
        project_id: str,
        version: int,
        *,
        input: Mapping[str, Any] | None = None,
        metadata: ExecutionMetadata | None = None,
        timeout_policy: TimeoutPolicy | None = None,
        retry_policy: RetryPolicy | None = None,
        dispatch: bool = True,
    ) -> Execution:
        """Pin ``version`` and queue a new execution of it with ``input``
        (a JSON object; entry nodes such as ``core.input`` read it), then
        hand it to the workers unless ``dispatch`` is false (the caller runs
        it itself, like the CLI's ``project run``).

        Never reads the draft: later draft edits or publishes cannot change
        what this execution runs. The retry policy defaults to no retries:
        repeating side effects must be opted into (AGENTS §47).
        """
        async with self._uow() as uow:
            snapshot = await uow.versions.get(project_id, version)
            if snapshot is None:
                raise ProjectVersionNotFoundError(
                    "Project version not found.",
                    details={"project_id": project_id, "version": version},
                )
            execution = Execution.create(
                project_id,
                snapshot,
                metadata=metadata,
                timeout_policy=timeout_policy,
                retry_policy=retry_policy,
                input=input,
            )
            execution.enqueue()
            await save_execution(uow, execution)
            await uow.commit()
        events = execution.pull_events()
        observe_execution(execution, events, self._metrics)
        await self._events.publish(events)
        if dispatch:
            await self._dispatch(execution.id)
        return execution

    async def dispatch_queued(self, *, older_than: timedelta, limit: int = 500) -> int:
        """Queue recovery: re-dispatch executions still QUEUED ``older_than``
        after creation — their job was lost (queue down at creation, Redis
        restarted empty, a job acknowledged by a worker that failed before
        starting the run). Duplicate protection makes this safe to repeat;
        the age keeps it off executions whose own dispatch is in flight or
        that a caller runs itself. Returns how many jobs were queued."""
        if self._queue is None:
            return 0
        async with self._uow() as uow:
            ids = await uow.executions.list_queued(
                created_before=utcnow() - older_than, limit=limit
            )
        queued = 0
        for execution_id in ids:
            if await self._queue.enqueue(Job(execution_id)):
                queued += 1
        return queued

    async def _dispatch(self, execution_id: str) -> None:
        if self._queue is None:
            return
        try:
            await self._queue.enqueue(Job(execution_id))
        except QueueUnavailableError:
            # Not the caller's failure: the execution is committed QUEUED,
            # and ``dispatch_queued`` picks it up once the queue is back.
            logger.warning(
                "execution_dispatch_failed",
                extra={"event": "execution_dispatch_failed"},
                exc_info=True,
            )

    async def get_execution(self, execution_id: str) -> Execution:
        async with self._uow() as uow:
            return await load_execution(uow, execution_id)
