"""Queue + workers against real Redis and real PostgreSQL (roadmap Phase 22).

Every scenario drives the real ``RedisExecutionQueue``, ``Worker``,
``ExecutionRuntime`` and database, then asserts on persisted execution state
and on what Redis holds. Each test gets its own key prefix, so tests share
one Redis container without sharing state.

Synchronization is event-driven, never by sleeping: nodes signal when they
start and block on events the test releases; ``SpyQueue`` reports each
acknowledgement, the point after which a job's handling is complete. A
worker crash is a cancelled ``Worker.run`` task; heartbeat expiry is the
deletion of the registration key (what its TTL does, without waiting for it).
"""

import asyncio
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import text

from akasha.api.app import create_app
from akasha.application.executions import ExecutionService
from akasha.application.queue import (
    Job,
    QueueUnavailableError,
    WorkerInfo,
    WorkerState,
)
from akasha.application.triggers import TriggerRegistry, TriggerService
from akasha.application.workflows import WorkflowService
from akasha.bootstrap import Container, create_container
from akasha.config import Settings
from akasha.domain.execution import ExecutionStatus, NodeExecutionStatus
from akasha.domain.graph import Edge, Graph, NodeInstance
from akasha.domain.node import (
    Node,
    NodeDefinition,
    NodeExecutionContext,
    NodeInput,
    NodeMetadata,
    NodeOutput,
    NodeResult,
    PortType,
)
from akasha.domain.primitives import new_id
from akasha.domain.workflow import WorkflowDraft
from akasha.infrastructure.database import Database
from akasha.infrastructure.persistence import SqlAlchemyUnitOfWork
from akasha.infrastructure.redis_queue import RedisExecutionQueue, encode_job
from akasha.runtime.runtime import ExecutionRuntime
from akasha.workers import Worker, WorkerManager
from tests.integration.fixtures import make_auth_kit

pytestmark = pytest.mark.usefixtures("clean_db")

_E = ExecutionStatus
# Upper bound for one scenario step: a deadlock fails instead of hanging.
# Never used to wait for a state.
GUARD = 10
# Dequeue block time. Bounds how long a stop request waits for an idle
# worker's blocked dequeue to return; it never paces a test.
POLL = 0.05


# -- nodes -----------------------------------------------------------------


class Gate:
    """Test node control: counts starts, blocks until released."""

    def __init__(self, *, open: bool = True) -> None:
        self.release = asyncio.Event()
        if open:
            self.release.set()
        self.started = asyncio.Event()
        self.starts = 0
        self.each_start: asyncio.Queue[None] = asyncio.Queue()
        self.cancelled_seen: list[bool] = []

    def node(self) -> type[Node]:
        gate = self

        class GateNode(Node):
            definition = NodeDefinition(
                metadata=NodeMetadata(
                    node_id="test.gate",
                    name="Gate",
                    description="Blocks until released.",
                    version="1.0.0",
                ),
                outputs=(NodeOutput("out", PortType.STRING),),
            )

            async def execute(self, context: NodeExecutionContext) -> NodeResult:
                gate.starts += 1
                gate.started.set()
                gate.each_start.put_nowait(None)
                try:
                    await gate.release.wait()
                finally:
                    gate.cancelled_seen.append(context.is_cancelled())
                return NodeResult({"out": "done"})

        return GateNode


class Sink(Node):
    """Downstream of the gate: a published graph needs connected nodes."""

    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="test.sink",
            name="Sink",
            description="Consumes text.",
            version="1.0.0",
        ),
        inputs=(NodeInput("text", PortType.STRING),),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        return NodeResult({})


# -- queue spy ---------------------------------------------------------------


class SpyQueue(RedisExecutionQueue):
    """The real queue, reporting each acknowledged execution id."""

    def __init__(self, client: Redis, *, prefix: str) -> None:
        super().__init__(client, prefix=prefix)
        self.acked: asyncio.Queue[str] = asyncio.Queue()

    async def ack(self, worker_id: str, job: Job) -> None:
        await super().ack(worker_id, job)
        self.acked.put_nowait(job.execution_id)

    async def next_ack(self) -> str:
        async with asyncio.timeout(GUARD):
            return await self.acked.get()


# -- harness ---------------------------------------------------------------


class Harness:
    def __init__(self, database: Database, redis: Redis, prefix: str) -> None:
        self.database = database
        self.redis = redis
        self.prefix = prefix
        self.queue = SpyQueue(redis, prefix=prefix)
        self.gate = Gate()
        self.catalog: Mapping[str, type[Node]] = {
            "test.gate": self.gate.node(),
            "test.sink": Sink,
        }
        self.executions = ExecutionService(self.uow, queue=self.queue)
        self._tasks: list[asyncio.Task[None]] = []

    def uow(self) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self.database)

    def runtime(self) -> ExecutionRuntime:
        return ExecutionRuntime(self.uow, self.catalog)

    def worker(self, **options: Any) -> Worker:
        options.setdefault("poll_seconds", POLL)
        return Worker(self.queue, self.runtime(), **options)

    def start(self, worker: Worker) -> asyncio.Task[None]:
        task = asyncio.create_task(worker.run())
        self._tasks.append(task)
        return task

    async def stop(self, worker: Worker, task: asyncio.Task[None]) -> None:
        worker.request_stop()
        async with asyncio.timeout(GUARD):
            await task

    async def publish(self) -> tuple[str, int]:
        catalog = {t: c.definition for t, c in self.catalog.items()}
        svc = WorkflowService(self.uow, catalog)
        workflow = await svc.create_workflow("workers")
        graph = Graph()
        gate, sink = NodeInstance.create("test.gate"), NodeInstance.create("test.sink")
        graph.add_node(gate)
        graph.add_node(sink)
        graph.add_edge(Edge(gate.id, "out", sink.id, "text"))
        await svc.update_draft(
            workflow.id,
            expected_revision=workflow.revision,
            name="workers",
            draft=WorkflowDraft(graph=graph),
        )
        version = await svc.publish_workflow(workflow.id)
        return workflow.id, version.version

    async def create(self, *, dispatch: bool = True) -> str:
        workflow_id, version = await self.publish()
        execution = await self.executions.create_execution(
            workflow_id, version, dispatch=dispatch
        )
        return execution.id

    async def status(self, execution_id: str) -> ExecutionStatus:
        return (await self.executions.get_execution(execution_id)).status

    async def held(self, worker_id: str) -> list[str]:
        held = await self.redis.lrange(f"{self.prefix}:queue:held:{worker_id}", 0, -1)
        return [str(item) for item in held]

    async def guarded(self) -> dict[str, str]:
        jobs = await self.redis.hgetall(f"{self.prefix}:queue:jobs")
        return {str(k): str(v) for k, v in jobs.items()}

    async def expire(self, worker_id: str) -> None:
        """What the registration TTL does after missed heartbeats."""
        await self.redis.delete(f"{self.prefix}:worker:{worker_id}")

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)


@pytest.fixture
async def redis(redis_url: str) -> AsyncIterator[Redis]:
    client = Redis.from_url(redis_url, decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def h(database: Database, redis: Redis) -> AsyncIterator[Harness]:
    harness = Harness(database, redis, new_id("t"))
    try:
        yield harness
    finally:
        await harness.close()


async def wait_for(event: asyncio.Event) -> None:
    async with asyncio.timeout(GUARD):
        await event.wait()


def worker_info(worker_id: str, state: WorkerState = WorkerState.RUNNING) -> WorkerInfo:
    now = datetime.now(UTC)
    return WorkerInfo(worker_id, "host", 1, state, 1, now, now, ())


# -- queue -----------------------------------------------------------------


async def test_enqueue_dequeue_ack_is_fifo_and_empties_the_queue(h: Harness) -> None:
    jobs = [Job(new_id("exec")) for _ in range(3)]
    for job in jobs:
        assert await h.queue.enqueue(job)
    assert await h.queue.depth() == 3

    taken = [await h.queue.dequeue("w1", block_seconds=POLL) for _ in jobs]

    assert taken == jobs  # oldest first
    assert await h.queue.depth() == 0
    assert len(await h.held("w1")) == 3
    for job in jobs:
        await h.queue.ack("w1", job)
    assert await h.held("w1") == []
    assert await h.guarded() == {}
    assert await h.queue.dequeue("w1", block_seconds=POLL) is None


async def test_duplicate_jobs_are_refused_while_queued_or_held(h: Harness) -> None:
    job = Job(new_id("exec"))
    assert await h.queue.enqueue(job)
    assert not await h.queue.enqueue(job)  # queued
    assert await h.queue.depth() == 1

    assert await h.queue.dequeue("w1", block_seconds=POLL) == job
    assert not await h.queue.enqueue(job)  # held
    assert await h.queue.depth() == 0

    await h.queue.ack("w1", job)
    assert await h.queue.enqueue(job)  # handled: may be queued again


async def test_undecodable_job_is_dropped_not_redelivered(h: Harness) -> None:
    await h.redis.lpush(f"{h.prefix}:queue:pending", '{"v": 99, "execution_id": "x"}')

    assert await h.queue.dequeue("w1", block_seconds=POLL) is None
    assert await h.held("w1") == []
    assert await h.queue.depth() == 0


async def test_unreachable_redis_is_a_queue_unavailable_error() -> None:
    queue = RedisExecutionQueue.from_url("redis://127.0.0.1:1/0")
    try:
        with pytest.raises(QueueUnavailableError) as caught:
            await queue.enqueue(Job("exec_1"))
        assert "127.0.0.1" not in caught.value.message
        assert await queue.ping() is not None
    finally:
        await queue.close()


# -- dispatch and processing -------------------------------------------------


async def test_created_execution_is_dispatched_and_run_by_a_worker(h: Harness) -> None:
    execution_id = await h.create()
    assert await h.queue.depth() == 1
    assert await h.status(execution_id) is _E.QUEUED

    worker = h.worker()
    task = h.start(worker)
    assert await h.queue.next_ack() == execution_id

    execution = await h.executions.get_execution(execution_id)
    assert execution.status is _E.COMPLETED
    assert {ne.status for ne in execution.node_executions.values()} == {
        NodeExecutionStatus.COMPLETED
    }
    assert await h.held(worker.worker_id) == []
    assert await h.guarded() == {}
    await h.stop(worker, task)


async def test_worker_runs_up_to_its_concurrency_at_once(h: Harness) -> None:
    h.gate.release.clear()
    ids = [await h.create() for _ in range(3)]
    worker = h.worker(concurrency=2)
    task = h.start(worker)
    async with asyncio.timeout(GUARD):
        for _ in range(2):
            await h.gate.each_start.get()

    # Two runs block on the gate; the third job waits in the queue. The
    # worker only dequeues with a free slot, so this is stable, not a race.
    assert len(await h.held(worker.worker_id)) == 2
    assert await h.queue.depth() == 1

    h.gate.release.set()
    assert sorted([await h.queue.next_ack() for _ in ids]) == sorted(ids)
    assert h.gate.starts == 3
    await h.stop(worker, task)


async def test_manual_run_without_dispatch_is_not_queued(h: Harness) -> None:
    execution_id = await h.create(dispatch=False)
    assert await h.queue.depth() == 0
    assert await h.status(execution_id) is _E.QUEUED


# -- failures, retries, recovery ---------------------------------------------


async def test_failed_job_is_acked_and_the_queued_execution_redispatched(
    h: Harness,
) -> None:
    execution_id = await h.create()
    outage = {"left": 1}

    def flaky_uow() -> SqlAlchemyUnitOfWork:
        if outage["left"]:
            outage["left"] -= 1
            raise ConnectionRefusedError("database down")
        return h.uow()

    worker = Worker(h.queue, ExecutionRuntime(flaky_uow, h.catalog), poll_seconds=POLL)
    task = h.start(worker)
    assert await h.queue.next_ack() == execution_id  # failed, acknowledged
    assert await h.status(execution_id) is _E.QUEUED  # the truth is intact
    assert await h.queue.depth() == 0

    manager = WorkerManager(worker, h.queue, h.executions, recovery_seconds=1e-6)
    assert await manager.recover() == (0, 1, 0)  # re-dispatched: the retry

    assert await h.queue.next_ack() == execution_id
    assert await h.status(execution_id) is _E.COMPLETED
    assert h.gate.starts == 1
    await h.stop(worker, task)


async def test_execution_created_while_redis_is_down_is_dispatched_later(
    h: Harness,
) -> None:
    down = RedisExecutionQueue.from_url("redis://127.0.0.1:1/0")
    try:
        workflow_id, version = await h.publish()
        execution = await ExecutionService(h.uow, queue=down).create_execution(
            workflow_id, version
        )
    finally:
        await down.close()
    assert await h.status(execution.id) is _E.QUEUED  # accepted anyway
    assert await h.queue.depth() == 0

    worker = h.worker()
    manager = WorkerManager(worker, h.queue, h.executions, recovery_seconds=1e-6)
    assert await manager.recover() == (0, 1, 0)
    assert await manager.recover() == (0, 0, 0)  # duplicate protection

    task = h.start(worker)
    assert await h.queue.next_ack() == execution.id
    assert await h.status(execution.id) is _E.COMPLETED
    await h.stop(worker, task)


async def test_fresh_queued_executions_are_not_redispatched(h: Harness) -> None:
    await h.create(dispatch=False)  # e.g. the CLI runs it itself
    manager = WorkerManager(h.worker(), h.queue, h.executions, recovery_seconds=3600)
    assert await manager.recover() == (0, 0, 0)
    assert await h.queue.depth() == 0


# -- heartbeat and registration ----------------------------------------------


async def test_heartbeat_registers_the_worker_with_an_expiring_key(h: Harness) -> None:
    worker = h.worker(concurrency=3, heartbeat_seconds=2)
    await worker.beat()

    [info] = await h.queue.workers()
    assert info.worker_id == worker.worker_id
    assert info.state is WorkerState.RUNNING
    assert info.concurrency == 3
    assert info.pid == os.getpid()
    assert info.active_executions == ()
    ttl_ms = await h.redis.pttl(f"{h.prefix}:worker:{worker.worker_id}")
    assert 0 < ttl_ms <= 6000  # three heartbeat intervals

    worker.request_stop()
    await worker.beat()
    [info] = await h.queue.workers()
    assert info.state is WorkerState.STOPPING


async def test_heartbeat_reports_the_executions_being_run(h: Harness) -> None:
    h.gate.release.clear()
    execution_id = await h.create()
    worker = h.worker()
    task = h.start(worker)
    await wait_for(h.gate.started)

    await worker.beat()
    [info] = await h.queue.workers()
    assert info.active_executions == (execution_id,)

    h.gate.release.set()
    await h.queue.next_ack()
    await h.stop(worker, task)
    assert await h.queue.workers() == []  # deregistered


async def test_expired_registration_is_not_listed_and_is_recovered(h: Harness) -> None:
    await h.queue.heartbeat(worker_info("w-dead"), ttl=60)
    await h.expire("w-dead")

    assert await h.queue.workers() == []
    assert await h.queue.recover() == 0
    assert await h.redis.smembers(f"{h.prefix}:workers") == set()


# -- graceful shutdown -------------------------------------------------------


async def test_graceful_stop_finishes_runs_and_takes_no_new_jobs(h: Harness) -> None:
    h.gate.release.clear()
    first = await h.create()
    worker = h.worker(shutdown_grace_seconds=GUARD)
    task = h.start(worker)
    await wait_for(h.gate.started)

    worker.request_stop()
    second = await h.create()  # arrives while draining
    h.gate.release.set()
    await h.stop(worker, task)

    assert await h.status(first) is _E.COMPLETED
    assert await h.status(second) is _E.QUEUED  # left for another worker
    assert await h.queue.depth() == 1
    assert await h.held(worker.worker_id) == []
    assert await h.queue.workers() == []


async def test_graceful_stop_cancels_runs_that_outlast_the_grace(h: Harness) -> None:
    h.gate.release.clear()
    execution_id = await h.create()
    worker = h.worker(shutdown_grace_seconds=0)
    task = h.start(worker)
    await wait_for(h.gate.started)

    await h.stop(worker, task)

    execution = await h.executions.get_execution(execution_id)
    assert execution.status is _E.CANCELLED  # never left RUNNING
    assert execution.cancellation is not None
    assert execution.cancellation.reason == "worker shutdown"
    assert h.gate.cancelled_seen == [True]  # node cleanup ran, saw the stop
    assert await h.held(worker.worker_id) == []
    assert await h.guarded() == {}


async def test_job_dequeued_while_stopping_is_handed_back(h: Harness) -> None:
    job = Job(new_id("exec"))
    worker = h.worker()
    await worker.beat()
    await h.queue.enqueue(job)
    assert await h.queue.dequeue(worker.worker_id, block_seconds=POLL) == job

    assert await h.queue.deregister(worker.worker_id) == 1
    assert await h.queue.depth() == 1
    assert await h.held(worker.worker_id) == []


# -- crash boundary ----------------------------------------------------------


async def test_jobs_of_a_dead_worker_are_redelivered(h: Harness) -> None:
    execution_id = await h.create()
    # A worker registered, took the job and died before running it.
    await h.queue.heartbeat(worker_info("w-dead"), ttl=60)
    assert await h.queue.dequeue("w-dead", block_seconds=POLL) == Job(execution_id)
    assert await h.queue.recover() == 0  # still alive: not touched

    await h.expire("w-dead")
    assert await h.queue.recover() == 1
    assert await h.held("w-dead") == []

    worker = h.worker()
    task = h.start(worker)
    assert await h.queue.next_ack() == execution_id
    assert await h.status(execution_id) is _E.COMPLETED
    await h.stop(worker, task)


async def test_crash_mid_run_never_runs_node_code_twice(h: Harness) -> None:
    h.gate.release.clear()
    execution_id = await h.create()
    crashed = h.worker()
    crash_task = h.start(crashed)
    await wait_for(h.gate.started)

    crash_task.cancel()  # the process dies: no ack, no deregistration
    await asyncio.gather(crash_task, return_exceptions=True)
    assert await h.status(execution_id) is _E.RUNNING  # §139, Phase 23
    assert await h.held(crashed.worker_id) == [encode_job(Job(execution_id))]

    await h.expire(crashed.worker_id)
    assert await h.queue.recover() == 1
    survivor = h.worker()
    task = h.start(survivor)
    assert await h.queue.next_ack() == execution_id  # skipped, acknowledged

    assert h.gate.starts == 1
    assert await h.status(execution_id) is _E.RUNNING
    assert await h.guarded() == {}
    await h.stop(survivor, task)


async def test_worker_crash_mid_run_is_recovered_and_resumed(h: Harness) -> None:
    """Phase 23 end to end: a worker dies mid-node; once its execution's
    heartbeat is stale, a surviving worker's recovery pass takes it over and
    the survivor resumes it. The finished upstream is not re-run; the
    interrupted (side-effect free) gate runs again."""
    h.gate.release.clear()
    execution_id = await h.create()
    crashed = h.worker()
    crash_task = h.start(crashed)
    await wait_for(h.gate.started)
    crash_task.cancel()
    await asyncio.gather(crash_task, return_exceptions=True)
    await h.expire(crashed.worker_id)
    async with h.database.engine.begin() as conn:  # the heartbeat went stale
        await conn.execute(
            text(
                "UPDATE executions SET heartbeat_at = now() - interval '1 hour'"
                " WHERE id = :id"
            ),
            {"id": execution_id},
        )

    survivor = h.worker()
    manager = WorkerManager(survivor, h.queue, h.executions, stale_seconds=60)
    h.gate.release.set()
    # Stale recovery requeues it; the dead worker's held job is redelivered.
    assert await manager.recover() == (1, 0, 1)
    assert await h.status(execution_id) is _E.QUEUED
    task = h.start(survivor)
    assert await h.queue.next_ack() == execution_id

    execution = await h.executions.get_execution(execution_id)
    assert execution.status is _E.COMPLETED
    assert h.gate.starts == 2
    gate = next(
        ne for ne in execution.node_executions.values() if ne.failures
    )  # the interrupted one
    assert [f.code for f in gate.failures] == ["NODE_INTERRUPTED"]
    assert await h.guarded() == {}
    await h.stop(survivor, task)


# -- duplicate delivery ------------------------------------------------------


async def test_duplicate_delivery_to_two_workers_runs_once(h: Harness) -> None:
    h.gate.release.clear()
    execution_id = await h.create()
    # A second copy, as recovery produces for a worker that only stalled.
    await h.redis.lpush(f"{h.prefix}:queue:pending", encode_job(Job(execution_id)))
    first = h.worker(concurrency=1)  # cannot take the copy itself
    first_task = h.start(first)
    await wait_for(h.gate.started)

    second = h.worker()  # its own runtime: another process
    second_task = h.start(second)
    assert await h.queue.next_ack() == execution_id  # the copy, refused

    h.gate.release.set()
    assert await h.queue.next_ack() == execution_id
    assert h.gate.starts == 1
    assert await h.status(execution_id) is _E.COMPLETED
    await h.stop(first, first_task)
    await h.stop(second, second_task)


async def test_redelivery_of_a_finished_execution_is_skipped(h: Harness) -> None:
    execution_id = await h.create()
    worker = h.worker()
    task = h.start(worker)
    await h.queue.next_ack()

    assert await h.queue.enqueue(Job(execution_id))
    assert await h.queue.next_ack() == execution_id
    assert h.gate.starts == 1
    assert await h.status(execution_id) is _E.COMPLETED
    await h.stop(worker, task)


# -- manager, container, API -------------------------------------------------


async def test_manager_start_stop_owns_the_worker(h: Harness) -> None:
    manager = WorkerManager(h.worker(), h.queue, h.executions, recovery_seconds=60)
    await manager.start()
    execution_id = await h.create()
    assert await h.queue.next_ack() == execution_id
    [info] = await manager.workers()
    assert info.worker_id == manager.worker.worker_id

    await manager.stop()
    await manager.stop()  # idempotent
    assert await manager.workers() == []
    assert await h.status(execution_id) is _E.COMPLETED


async def test_container_wires_queue_worker_and_readiness(
    db_url: str, redis_url: str
) -> None:
    settings = Settings(
        environment="test",
        database_url=db_url,
        redis_url=SecretStr(redis_url),
        queue_prefix=new_id("t")[:24].replace("_", "-"),
        worker_poll_seconds=POLL,
    )
    container = create_container(settings)
    assert container.workers is not None and container.queue is not None
    try:
        assert await container.readiness_failures() == {}
        await container.start()
        [info] = await container.workers.workers()
        assert info.state is WorkerState.RUNNING
    finally:
        await container.shutdown()
    reader = RedisExecutionQueue.from_url(redis_url, prefix=settings.queue_prefix)
    try:
        assert await reader.workers() == []
    finally:
        await reader.close()


async def test_container_reports_an_unreachable_queue_not_ready(db_url: str) -> None:
    container = create_container(
        Settings(
            environment="test",
            database_url=db_url,
            redis_url=SecretStr("redis://127.0.0.1:1/0"),
        )
    )
    try:
        failures = await container.readiness_failures()
    finally:
        await container.shutdown()
    assert set(failures) == {"queue"}


@asynccontextmanager
async def api_client(
    h: Harness, manager: WorkerManager
) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(environment="test", log_level="CRITICAL")
    auth_kit = make_auth_kit(h.database)
    catalog = {t: c.definition for t, c in h.catalog.items()}
    # No ``database``: the lifespan's shutdown would dispose the shared one.
    container = Container(
        settings=settings,
        workflows=WorkflowService(h.uow, catalog),
        executions=h.executions,
        triggers=TriggerService(h.executions),
        trigger_registry=TriggerRegistry(h.uow),
        queue=None,
        workers=manager,
        **auth_kit.container_fields(),
    )
    app = create_app(settings=settings, container=container)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers=await auth_kit.admin_headers(),
        ) as client,
    ):
        yield client


async def test_execution_outlives_the_http_request(h: Harness) -> None:
    """Acceptance: the request returns while the run is still going; the
    worker finishes it after the response was sent."""
    h.gate.release.clear()
    workflow_id, version = await h.publish()
    manager = WorkerManager(h.worker(), h.queue, h.executions, recovery_seconds=60)
    async with api_client(h, manager) as api:
        response = await api.post(
            f"/api/v1/workflows/{workflow_id}/versions/{version}/execute", json={}
        )
        assert response.status_code == 201
        execution_id = response.json()["id"]
        assert response.json()["status"] == "queued"

        await wait_for(h.gate.started)  # running after the response
        got = await api.get(f"/api/v1/executions/{execution_id}")
        assert got.json()["status"] == "running"

        h.gate.release.set()
        assert await h.queue.next_ack() == execution_id
        got = await api.get(f"/api/v1/executions/{execution_id}")
        assert got.json()["status"] == "completed"
