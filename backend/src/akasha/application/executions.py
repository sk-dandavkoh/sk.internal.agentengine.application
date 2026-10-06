"""Execution use cases (roadmap Phase 08): create and inspect executions.

Creating an execution pins one immutable ProjectVersion and records it as
QUEUED — accepted for execution. *Running* it is the runtime's job
(``akasha.runtime``), done by a worker (Phase 22): after the commit the
execution is dispatched as a ``Job`` on the ``ExecutionQueue``. Every
trigger converges on ``create_execution``.
"""

import logging
from collections.abc import Collection, Mapping
from datetime import timedelta
from typing import Any

from akasha.application.events import EventPublisher
from akasha.application.persistence import ExecutionSummary, UnitOfWork
from akasha.application.projects import (
    Page,
    ProjectVersionNotFoundError,
    UowFactory,
    check_page,
    load_project,
    to_page,
)
from akasha.application.queue import ExecutionQueue, Job, QueueUnavailableError
from akasha.domain.event import Event
from akasha.domain.execution import (
    Execution,
    ExecutionMetadata,
    ExecutionStatus,
    LogLevel,
    NodeLogEntry,
    RetryPolicy,
    TimeoutPolicy,
)
from akasha.domain.limits import ResourceLimitError, ResourceLimits
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
    """Stage the aggregate, its recorded events and node log lines in
    ``uow``'s transaction.

    Both stay pending on the aggregate: the caller pulls them
    (``pull_events``/``pull_logs``) only after ``uow.commit()`` succeeded.
    """
    await uow.executions.save(execution)
    await uow.events.append(execution.pending_events)
    await uow.node_logs.append(execution.pending_logs)


class ExecutionService:
    def __init__(
        self,
        uow: UowFactory,
        events: EventPublisher | None = None,
        metrics: EngineMetrics | None = None,
        queue: ExecutionQueue | None = None,
        limits: ResourceLimits | None = None,
    ) -> None:
        self._uow = uow
        self._limits = limits or ResourceLimits()
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

        Admission control (Phase 24): unset timeouts take the engine's
        limits. A policy above a limit, a graph that is too large or too
        deep, or a project at its active-execution cap raises
        ``ResourceLimitError`` and nothing is stored.
        """
        try:
            return await self._create(
                project_id,
                version,
                input=input,
                metadata=metadata,
                timeout_policy=timeout_policy,
                retry_policy=retry_policy,
                dispatch=dispatch,
            )
        except ResourceLimitError as exc:
            self._metrics.resource_limit_exceeded.labels(exc.limit.value).inc()
            logger.warning(
                "execution_rejected",
                extra={
                    "event": "execution_rejected",
                    "project_id": project_id,
                    "project_version": version,
                    "reason": exc.code,
                    "limit": exc.limit.value,
                },
            )
            raise

    async def _create(
        self,
        project_id: str,
        version: int,
        *,
        input: Mapping[str, Any] | None,
        metadata: ExecutionMetadata | None,
        timeout_policy: TimeoutPolicy | None,
        retry_policy: RetryPolicy | None,
        dispatch: bool,
    ) -> Execution:
        timeout_policy, retry_policy = self._limits.policies(
            timeout_policy, retry_policy
        )
        async with self._uow() as uow:
            snapshot = await uow.versions.get(project_id, version)
            if snapshot is None:
                raise ProjectVersionNotFoundError(
                    "Project version not found.",
                    details={"project_id": project_id, "version": version},
                )
            self._limits.check_graph(snapshot.graph)
            self._limits.check_capacity(
                await uow.executions.count_unfinished(project_id)
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

    async def list_executions(
        self,
        project_id: str,
        *,
        version: int | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> Page[ExecutionSummary]:
        """The project's executions newest first; only those of published
        ``version`` when given (an unpublished one simply has none)."""
        check_page(offset, limit)
        async with self._uow() as uow:
            await load_project(uow, project_id)
            rows = await uow.executions.list_summaries(
                project_id=project_id,
                project_version=version,
                offset=offset,
                limit=limit + 1,
            )
        return to_page(rows, offset, limit)

    async def list_recent(
        self,
        *,
        visible_to: str | None = None,
        statuses: Collection[ExecutionStatus] = (),
        offset: int = 0,
        limit: int = 50,
    ) -> Page[ExecutionSummary]:
        """Executions across projects, newest first: every project's, or
        only those of the projects ``visible_to`` that user; optionally only
        ``statuses``."""
        check_page(offset, limit)
        async with self._uow() as uow:
            rows = await uow.executions.list_summaries(
                visible_to=visible_to,
                statuses=statuses,
                offset=offset,
                limit=limit + 1,
            )
        return to_page(rows, offset, limit)

    async def count_by_status(
        self, *, visible_to: str | None = None
    ) -> dict[ExecutionStatus, int]:
        """Executions per status, every status present (zero when none)."""
        async with self._uow() as uow:
            counts = await uow.executions.count_by_status(visible_to=visible_to)
        return {status: counts.get(status, 0) for status in ExecutionStatus}

    async def list_events(
        self, execution_id: str, *, after: int = 0, limit: int = 100
    ) -> Page[Event]:
        """Stored events of one execution, ascending by position. Pass the
        last seen ``position`` as ``after`` to resume (e.g. after a
        WebSocket reconnect, AGENTS §82). ``Page.offset`` is ``after``."""
        return (await self.follow_events(execution_id, after=after, limit=limit))[1]

    async def list_logs(
        self,
        execution_id: str,
        *,
        after: int = 0,
        limit: int = 100,
        node_id: str | None = None,
        min_level: LogLevel = LogLevel.DEBUG,
        contains: str | None = None,
    ) -> Page[NodeLogEntry]:
        """Stored node log lines of one execution, ascending by position,
        optionally of one node, at or above ``min_level`` and with
        ``contains`` in the message. Resumable
        like ``list_events``: pass the last seen position as ``after``."""
        check_page(after, limit)
        async with self._uow() as uow:
            await load_execution(uow, execution_id)  # 404 before "no lines"
            rows = await uow.node_logs.list(
                execution_id,
                after=after,
                limit=limit + 1,
                node_id=node_id,
                levels=() if min_level is LogLevel.DEBUG else min_level.at_least(),
                contains=contains,
            )
        return to_page(rows, after, limit)

    async def follow_events(
        self, execution_id: str, *, after: int = 0, limit: int = 100
    ) -> tuple[Execution, Page[Event]]:
        """``list_events`` plus the execution, read *before* its events: a
        terminal state commits together with its final events, so when the
        execution is terminal and the page has no more, the stream is
        complete (the live gateway's end condition)."""
        check_page(after, limit)
        async with self._uow() as uow:
            execution = await load_execution(uow, execution_id)
            rows = await uow.events.list(
                execution_id=execution_id, after=after, limit=limit + 1
            )
        return execution, to_page(rows, after, limit)
