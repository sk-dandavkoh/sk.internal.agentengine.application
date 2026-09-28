"""Execution persistence against real PostgreSQL (roadmap Phase 08).

Repository round-trip fidelity, CAS, constraints, snapshot-consistent
loading under concurrent writers, and the 0002 → 0003 upgrade path with a
real execution afterwards. No persistence mocks (backend/AGENTS.md §54).
"""

import asyncio
import json
import math
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from akasha.application.executions import ExecutionService
from akasha.application.persistence import ConcurrentModificationError
from akasha.domain.execution import (
    Execution,
    ExecutionError,
    ExecutionMetadata,
    ExecutionStatus,
    NodeExecutionStatus,
    RetryPolicy,
    TimeoutPolicy,
)
from akasha.domain.graph import Edge, Graph, NodeInstance
from akasha.domain.node import (
    Node,
    NodeDefinition,
    NodeErrorKind,
    NodeExecutionContext,
    NodeInput,
    NodeMetadata,
    NodeOutput,
    NodeResult,
    PortType,
)
from akasha.domain.primitives import new_id
from akasha.domain.project import Project, ProjectVersion
from akasha.domain.trigger import TriggerType
from akasha.infrastructure.database import (
    Database,
    normalize_async_url,
    normalize_sync_url,
)
from akasha.infrastructure.graph_serialization import serialize_graph
from akasha.infrastructure.persistence import SqlAlchemyUnitOfWork
from akasha.runtime.runtime import ExecutionRuntime

UowFactory = Callable[[], AbstractAsyncContextManager[SqlAlchemyUnitOfWork]]

pytestmark = pytest.mark.usefixtures("clean_db")

_BACKEND_DIR = Path(__file__).resolve().parents[2]
T0 = datetime(2100, 1, 1, tzinfo=UTC)


def two_node_graph() -> tuple[Graph, str, str]:
    a = NodeInstance.create("test.emit")
    b = NodeInstance.create("test.echo")
    graph = Graph()
    graph.add_node(a)
    graph.add_node(b)
    graph.add_edge(Edge(a.id, "out", b.id, "text"))
    return graph, a.id, b.id


async def published(uow: UowFactory) -> tuple[Project, ProjectVersion]:
    project = Project.create("demo")
    project.draft.graph = two_node_graph()[0]
    version = project.publish_draft()
    async with uow() as ctx:
        await ctx.projects.save(project)
        await ctx.versions.save(project.id, version)
        await ctx.commit()
    return project, version


async def saved(uow: UowFactory, execution: Execution) -> Execution:
    async with uow() as ctx:
        await ctx.executions.save(execution)
        await ctx.commit()
    return execution


async def test_full_aggregate_round_trip(uow: UowFactory) -> None:
    project, version = await published(uow)
    a, b = sorted(version.graph.nodes)
    execution = Execution.create(
        project.id,
        version,
        metadata=ExecutionMetadata(
            trigger_type=TriggerType.API, correlation_id="req-42"
        ),
        retry_policy=RetryPolicy(
            max_attempts=3, initial_backoff=timedelta(seconds=0.5)
        ),
        timeout_policy=TimeoutPolicy(execution_timeout=timedelta(minutes=2)),
        now=T0,
    )
    execution.enqueue()
    execution.start(now=T0)
    execution.mark_node_ready(a)
    execution.start_node(a, now=T0)
    transient = ExecutionError(
        code="RATE_LIMITED",
        message="slow down",
        kind=NodeErrorKind.TRANSIENT,
        node_id=a,
        details={"retry_after": "1"},
    )
    execution.retry_node(a, transient, now=T0)
    execution.start_node(a, now=T0 + timedelta(days=1))
    execution.complete_node(
        a,
        outputs={"text": "ü", "blob": b"\x00\x01", "nested": {"n": [1, None]}},
        now=T0 + timedelta(days=1),
    )
    execution.request_cancellation(reason="stop", now=T0 + timedelta(days=2))
    execution.cancel(now=T0 + timedelta(days=2))
    await saved(uow, execution)

    async with uow() as ctx:
        loaded = await ctx.executions.get(execution.id)
    assert loaded is not None
    assert loaded.status is ExecutionStatus.CANCELLED
    assert loaded.revision == execution.revision == 1
    assert loaded.project_version.id == version.id
    assert loaded.metadata == execution.metadata
    assert loaded.retry_policy == execution.retry_policy
    assert loaded.timeout_policy == execution.timeout_policy
    assert loaded.cancellation == execution.cancellation
    assert (loaded.created_at, loaded.started_at, loaded.finished_at) == (
        execution.created_at,
        execution.started_at,
        execution.finished_at,
    )
    na, nb = loaded.node(a), loaded.node(b)
    assert (na.status, na.attempt, na.failures) == (
        NodeExecutionStatus.COMPLETED,
        2,
        [transient],
    )
    # bytes are not JSON: persisted as a type stub, never silently dropped.
    assert na.outputs == {
        "text": "ü",
        "blob": {"$unpersisted": "bytes"},
        "nested": {"n": [1, None]},
    }
    assert (nb.status, nb.attempt, nb.outputs) == (
        NodeExecutionStatus.CANCELLED,
        0,
        None,
    )


async def test_failed_execution_error_round_trips(uow: UowFactory) -> None:
    project, version = await published(uow)
    a, _ = sorted(version.graph.nodes)
    execution = Execution.create(project.id, version, now=T0)
    execution.enqueue()
    execution.start(now=T0)
    execution.mark_node_ready(a)
    execution.start_node(a, now=T0)
    error = ExecutionError(
        code="BOOM", message="no", kind=NodeErrorKind.PERMANENT, node_id=a
    )
    execution.fail_node(a, error, now=T0)
    execution.fail(error, now=T0)
    await saved(uow, execution)
    async with uow() as ctx:
        loaded = await ctx.executions.get(execution.id)
    assert loaded is not None
    assert loaded.error == error and loaded.node(a).error == error


async def test_non_finite_output_cannot_break_persistence(uow: UowFactory) -> None:
    project, version = await published(uow)
    a, _ = sorted(version.graph.nodes)
    execution = Execution.create(project.id, version, now=T0)
    execution.enqueue()
    execution.start(now=T0)
    execution.mark_node_ready(a)
    execution.start_node(a, now=T0)
    execution.complete_node(a, outputs={"x": math.nan}, now=T0)
    await saved(uow, execution)  # JSONB rejects NaN; must not raise
    async with uow() as ctx:
        loaded = await ctx.executions.get(execution.id)
    assert loaded is not None
    assert loaded.node(a).outputs == {"$unpersisted": "value not representable as JSON"}


async def test_save_is_compare_and_swap_on_revision(uow: UowFactory) -> None:
    project, version = await published(uow)
    execution = Execution.create(project.id, version)
    execution.enqueue()
    await saved(uow, execution)
    async with uow() as ctx:
        first = await ctx.executions.get(execution.id)
    async with uow() as ctx:
        second = await ctx.executions.get(execution.id)
    assert first is not None and second is not None

    first.start(now=T0)
    await saved(uow, first)
    second.cancel(reason="stale writer", now=T0)
    async with uow() as ctx:
        with pytest.raises(ConcurrentModificationError):
            await ctx.executions.save(second)

    async with uow() as ctx:
        loaded = await ctx.executions.get(execution.id)
    assert loaded is not None
    assert (loaded.status, loaded.revision) == (ExecutionStatus.RUNNING, 2)
    assert loaded.cancellation is None


async def test_duplicate_insert_is_a_conflict(uow: UowFactory) -> None:
    project, version = await published(uow)
    execution = Execution.create(project.id, version)
    await saved(uow, execution)
    clone = Execution(id=execution.id, project_id=project.id, project_version=version)
    async with uow() as ctx:
        with pytest.raises(ConcurrentModificationError):
            await ctx.executions.save(clone)


async def test_execution_must_reference_a_published_version(uow: UowFactory) -> None:
    project = Project.create("unpublished")
    async with uow() as ctx:
        await ctx.projects.save(project)
        await ctx.commit()
    orphan = Execution.create(project.id, project.draft.publish(version=1))
    async with uow() as ctx:
        with pytest.raises(IntegrityError):
            await ctx.executions.save(orphan)


async def test_version_with_executions_is_protected(
    uow: UowFactory, database: Database
) -> None:
    project, version = await published(uow)
    await saved(uow, Execution.create(project.id, version))
    with pytest.raises(IntegrityError):
        async with database.engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM project_versions WHERE id = :id"), {"id": version.id}
            )


async def test_status_check_constraint(uow: UowFactory, database: Database) -> None:
    project, version = await published(uow)
    execution = await saved(uow, Execution.create(project.id, version))
    with pytest.raises(IntegrityError):
        async with database.engine.begin() as conn:
            await conn.execute(
                text("UPDATE executions SET status = 'done' WHERE id = :id"),
                {"id": execution.id},
            )


async def test_load_never_sees_a_torn_aggregate(
    uow: UowFactory, database: Database
) -> None:
    """Regression: the execution row and node rows are two SELECTs. A
    reader must wait for a writer that already updated the execution row,
    then see its committed state for *both* (found by the two-runtime race
    test: a QUEUED execution with READY nodes failed rehydration)."""
    project, version = await published(uow)
    a, _ = sorted(version.graph.nodes)
    execution = Execution.create(project.id, version)
    execution.enqueue()
    await saved(uow, execution)

    async with uow() as writer:
        mine = await writer.executions.get(execution.id)
        assert mine is not None
        mine.start(now=T0)
        mine.mark_node_ready(a)
    async with uow() as writer, uow() as reader:
        await writer.executions.save(mine)  # row updated, not committed
        blocked = asyncio.create_task(reader.executions.get(execution.id))

        async def lock_waiters() -> int:
            async with database.engine.connect() as conn:
                count: int = (
                    await conn.execute(
                        text(
                            "SELECT count(*) FROM pg_stat_activity"
                            " WHERE wait_event_type = 'Lock'"
                        )
                    )
                ).scalar_one()
            return count

        # Bounded polling on PostgreSQL's lock table (not observable via an
        # asyncio primitive); never a blind sleep.
        for _ in range(1000):
            if await lock_waiters():
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("reader did not wait for the uncommitted writer")
        await writer.commit()
        seen = await blocked

    assert seen is not None
    assert seen.status is ExecutionStatus.RUNNING
    assert seen.node(a).status is NodeExecutionStatus.READY


# -- migration upgrade path ----------------------------------------------------------


class Emit(Node):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="test.emit", name="Emit", description="Emits.", version="1.0.0"
        ),
        outputs=(NodeOutput("out", PortType.STRING),),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        return NodeResult({"out": "hello"})


class Echo(Node):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="test.echo", name="Echo", description="Echoes.", version="1.0.0"
        ),
        inputs=(NodeInput("text", PortType.STRING),),
        outputs=(NodeOutput("out", PortType.STRING),),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        return NodeResult({"out": context.inputs["text"]})


def _alembic(verb: str, db_url: str, target: str) -> None:
    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", normalize_sync_url(db_url))
    getattr(command, verb)(cfg, target)


async def test_upgrade_from_0002_then_execute_existing_version(db_url: str) -> None:
    """Previous schema + a version published before executions existed →
    0003 → that version executes end to end (AGENTS §18)."""
    admin = Database(normalize_async_url(db_url))
    scratch = "akasha_migration_0003"
    try:
        async with admin.engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text(f"DROP DATABASE IF EXISTS {scratch}"))
            await conn.execute(text(f"CREATE DATABASE {scratch}"))
        scratch_url = f"{db_url.rpartition('/')[0]}/{scratch}"
        await asyncio.to_thread(
            _alembic, "upgrade", scratch_url, "0002_project_revision"
        )

        graph, _, b = two_node_graph()
        project_id = new_id("proj")
        db = Database(normalize_async_url(scratch_url))
        try:
            async with db.engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO projects (id, name, status, draft_graph,"
                        " draft_triggers, draft_dependencies, current_version,"
                        " created_at, updated_at) VALUES (:id, 'old', 'published',"
                        " '{\"nodes\": [], \"edges\": []}', '[]', '[]', 1,"
                        " now(), now())"
                    ),
                    {"id": project_id},
                )
                await conn.execute(
                    text(
                        "INSERT INTO project_versions (id, project_id, version,"
                        " schema_version, graph, triggers, dependencies,"
                        " published_at) VALUES (:id, :pid, 1, 1,"
                        " CAST(:graph AS jsonb), '[]', '[]', now())"
                    ),
                    {
                        "id": new_id("pver"),
                        "pid": project_id,
                        "graph": json.dumps(serialize_graph(graph)),
                    },
                )
            await asyncio.to_thread(_alembic, "upgrade", scratch_url, "head")

            def factory() -> SqlAlchemyUnitOfWork:
                return SqlAlchemyUnitOfWork(db)

            queued = await ExecutionService(factory).create_execution(project_id, 1)
            runtime = ExecutionRuntime(factory, {"test.emit": Emit, "test.echo": Echo})
            result = await runtime.run(queued.id)
            assert result.status is ExecutionStatus.COMPLETED
            assert result.node(b).outputs == {"out": "hello"}
        finally:
            await db.dispose()
    finally:
        async with admin.engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text(f"DROP DATABASE IF EXISTS {scratch}"))
        await admin.dispose()
