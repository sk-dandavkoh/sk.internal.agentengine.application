"""Execution dispatch contract (roadmap Phase 22, architecture §71/§72).

The execution row in PostgreSQL is the durable truth; a ``Job`` in the
queue is only the ephemeral *signal* "execution X is QUEUED, run it".
Losing a job therefore never loses an execution: ``ExecutionService``
re-dispatches executions that stay QUEUED (queue recovery), and running a
job twice is harmless because the runtime only runs QUEUED executions and
every save is compare-and-swap.

Delivery is at-least-once: a job is held by the worker that dequeued it
until acknowledged; the jobs of a worker whose heartbeat expired are handed
back to the queue. No application or runtime code sees the queue's
technology — ``akasha.infrastructure.redis_queue`` implements this protocol.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from akasha.errors import AkashaError


class QueueUnavailableError(AkashaError):
    """The queue backend could not be reached or answered with an error."""

    code = "QUEUE_UNAVAILABLE"
    http_status = 503


@dataclass(frozen=True)
class Job:
    """Run request for one QUEUED execution. The execution id is the job's
    identity: at most one job per execution is queued or held at a time."""

    execution_id: str


class WorkerState(StrEnum):
    RUNNING = "running"  # taking jobs
    STOPPING = "stopping"  # draining: no new jobs, finishing held ones


@dataclass(frozen=True)
class WorkerInfo:
    """A worker's registration, refreshed by every heartbeat."""

    worker_id: str
    hostname: str
    pid: int
    state: WorkerState
    concurrency: int
    started_at: datetime
    heartbeat_at: datetime
    active_executions: tuple[str, ...]


class ExecutionQueue(Protocol):
    async def enqueue(self, job: Job) -> bool:
        """Queue ``job`` unless a job for the same execution is already
        queued or held (duplicate protection). Returns whether it queued."""
        ...

    async def dequeue(self, worker_id: str, *, block_seconds: float) -> Job | None:
        """Hand the oldest job to ``worker_id``, waiting at most ``block_seconds``
        seconds. The job stays held by that worker until ``ack``."""
        ...

    async def ack(self, worker_id: str, job: Job) -> None:
        """Forget a held job; the execution may be queued again later."""
        ...

    async def heartbeat(self, info: WorkerInfo, *, ttl: float) -> None:
        """Register or refresh ``info``; it expires after ``ttl`` seconds."""
        ...

    async def deregister(self, worker_id: str) -> int:
        """Remove a stopping worker; jobs it still holds go back to the
        queue. Returns how many."""
        ...

    async def recover(self) -> int:
        """Hand the jobs held by workers whose heartbeat expired back to the
        queue. Returns how many."""
        ...

    async def workers(self) -> list[WorkerInfo]:
        """Live workers, ordered by id."""
        ...

    async def depth(self) -> int:
        """Jobs waiting (not held)."""
        ...
