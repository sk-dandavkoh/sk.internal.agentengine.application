"""Redis-backed ``ExecutionQueue`` (roadmap Phase 22, architecture §24.2).

Reliable-queue layout, all keys under one prefix (one installation, or one
test, per prefix):

    {p}:queue:pending       list  waiting jobs; LPUSH in, served from the right
    {p}:queue:held:{w}      list  jobs worker w dequeued and not yet acked
    {p}:queue:jobs          hash  execution id -> job, while queued or held
    {p}:workers             set   registered worker ids
    {p}:worker:{w}          hash  w's registration; expires unless heartbeat

A job moves pending -> held atomically (BLMOVE), so a crash between dequeue
and processing cannot drop it: it stays in the dead worker's held list until
``recover`` sees the registration expired. The ``jobs`` hash is the duplicate
guard; enqueue checks and pushes in one script.

Redis holds signals only; losing all of it loses no execution (see
``akasha.application.queue``).
"""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import cast

from redis.asyncio import Redis
from redis.exceptions import RedisError

from akasha.application.queue import (
    Job,
    QueueUnavailableError,
    WorkerInfo,
    WorkerState,
)

logger = logging.getLogger("akasha.infrastructure.redis_queue")

_JOB_VERSION = 1

# KEYS: jobs hash, pending list. ARGV: execution id, payload.
_ENQUEUE = """
if redis.call('HSETNX', KEYS[1], ARGV[1], ARGV[2]) == 1 then
  redis.call('LPUSH', KEYS[2], ARGV[2])
  return 1
end
return 0
"""


def encode_job(job: Job) -> str:
    return json.dumps({"v": _JOB_VERSION, "execution_id": job.execution_id})


def decode_job(payload: str) -> Job | None:
    """The job, or None for a payload this version does not understand."""
    try:
        doc = json.loads(payload)
    except ValueError:
        return None
    if (
        not isinstance(doc, dict)
        or doc.get("v") != _JOB_VERSION
        or not isinstance(doc.get("execution_id"), str)
    ):
        return None
    return Job(doc["execution_id"])


@contextmanager
def _unavailable() -> Iterator[None]:
    try:
        yield
    except RedisError as exc:
        # The URL may carry a password: the message never includes it.
        raise QueueUnavailableError(
            "The execution queue is unavailable.",
            details={"reason": type(exc).__name__},
        ) from exc


class RedisExecutionQueue:
    def __init__(self, client: "Redis", *, prefix: str = "akasha") -> None:
        self._redis = client
        self._pending = f"{prefix}:queue:pending"
        self._jobs = f"{prefix}:queue:jobs"
        self._held_prefix = f"{prefix}:queue:held:"
        self._workers = f"{prefix}:workers"
        self._worker_prefix = f"{prefix}:worker:"
        self._enqueue = client.register_script(_ENQUEUE)

    @classmethod
    def from_url(cls, url: str, *, prefix: str = "akasha") -> "RedisExecutionQueue":
        return cls(Redis.from_url(url, decode_responses=True), prefix=prefix)

    async def enqueue(self, job: Job) -> bool:
        with _unavailable():
            added = await self._enqueue(
                keys=[self._jobs, self._pending],
                args=[job.execution_id, encode_job(job)],
            )
        return bool(added)

    async def dequeue(self, worker_id: str, *, block_seconds: float) -> Job | None:
        held = self._held_prefix + worker_id
        with _unavailable():
            # Redis takes fractional block timeouts (>= 6.0); redis-py
            # annotates the argument as int.
            payload = await self._redis.blmove(
                self._pending, held, cast("int", block_seconds), "RIGHT", "LEFT"
            )
        if payload is None:
            return None
        job = decode_job(_text(payload))
        if job is None:
            # Not ours to interpret (a newer engine's job, garbage): drop it
            # instead of redelivering it forever.
            logger.error(
                "queue_job_undecodable", extra={"event": "queue_job_undecodable"}
            )
            with _unavailable():
                await self._redis.lrem(held, 1, _text(payload))
        return job

    async def ack(self, worker_id: str, job: Job) -> None:
        with _unavailable():
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.lrem(self._held_prefix + worker_id, 1, encode_job(job))
                pipe.hdel(self._jobs, job.execution_id)
                await pipe.execute()

    async def heartbeat(self, info: WorkerInfo, *, ttl: float) -> None:
        key = self._worker_prefix + info.worker_id
        with _unavailable():
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.hset(
                    key,
                    mapping={
                        "hostname": info.hostname,
                        "pid": info.pid,
                        "state": info.state.value,
                        "concurrency": info.concurrency,
                        "started_at": info.started_at.isoformat(),
                        "heartbeat_at": info.heartbeat_at.isoformat(),
                        "active_executions": json.dumps(info.active_executions),
                    },
                )
                pipe.pexpire(key, max(1, round(ttl * 1000)))
                pipe.sadd(self._workers, info.worker_id)
                await pipe.execute()

    async def deregister(self, worker_id: str) -> int:
        with _unavailable():
            returned = await self._return_held(worker_id)
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.delete(self._worker_prefix + worker_id)
                pipe.srem(self._workers, worker_id)
                await pipe.execute()
        return returned

    async def recover(self) -> int:
        recovered = 0
        with _unavailable():
            for worker_id in await self._worker_ids():
                if await self._redis.exists(self._worker_prefix + worker_id):
                    continue
                # Expired: the worker died (or stalled past its TTL — then its
                # runs race the redelivered jobs, and the runtime's
                # compare-and-swap lets exactly one of them own each run).
                recovered += await self._return_held(worker_id)
                await self._redis.srem(self._workers, worker_id)
        return recovered

    async def workers(self) -> list[WorkerInfo]:
        with _unavailable():
            ids = await self._worker_ids()
            async with self._redis.pipeline(transaction=False) as pipe:
                for worker_id in ids:
                    pipe.hgetall(self._worker_prefix + worker_id)
                rows = cast("list[dict[str, str]]", await pipe.execute())
        return [
            _worker_info(worker_id, row)
            for worker_id, row in zip(ids, rows, strict=True)
            if row  # registration expired, not yet recovered
        ]

    async def depth(self) -> int:
        with _unavailable():
            return int(await self._redis.llen(self._pending))

    async def ping(self) -> str | None:
        """Readiness check: None when reachable, else the reason."""
        try:
            await self._redis.ping()
        except RedisError as exc:
            return f"redis unreachable ({type(exc).__name__})"
        return None

    async def close(self) -> None:
        await self._redis.aclose()

    async def _worker_ids(self) -> list[str]:
        return sorted(map(_text, await self._redis.smembers(self._workers)))

    async def _return_held(self, worker_id: str) -> int:
        """Move every job ``worker_id`` holds back to the serving end of the
        pending list: redelivered work goes first."""
        held = self._held_prefix + worker_id
        moved = 0
        while await self._redis.lmove(held, self._pending, "RIGHT", "RIGHT"):
            moved += 1
        return moved


def _text(value: bytes | str) -> str:
    """The client decodes responses (``from_url``); a client built without
    that hands bytes, which are decoded here instead of misread."""
    return value if isinstance(value, str) else value.decode()


def _worker_info(worker_id: str, row: dict[str, str]) -> WorkerInfo:
    return WorkerInfo(
        worker_id=worker_id,
        hostname=row["hostname"],
        pid=int(row["pid"]),
        state=WorkerState(row["state"]),
        concurrency=int(row["concurrency"]),
        started_at=datetime.fromisoformat(row["started_at"]),
        heartbeat_at=datetime.fromisoformat(row["heartbeat_at"]),
        active_executions=tuple(json.loads(row["active_executions"])),
    )
