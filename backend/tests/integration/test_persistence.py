"""Integration tests for the persistence foundation against real PostgreSQL.

Everything here exercises the actual database: migrations, repository CRUD,
transaction boundaries, constraints, JSONB round-trip fidelity and immutable
published versions. No mocks for persistence behavior (backend/AGENTS.md
§54/§61).
"""

import asyncio
import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from akasha.application.persistence import ConcurrentModificationError
from akasha.domain.graph import Edge, Graph, NodeInstance, Position
from akasha.domain.primitives import new_id, utcnow
from akasha.domain.trigger import Trigger, TriggerType
from akasha.domain.workflow import Dependency, Workflow, WorkflowStatus, WorkflowVersion
from akasha.infrastructure.database import (
    Database,
    normalize_async_url,
    normalize_sync_url,
)
from akasha.infrastructure.persistence import SqlAlchemyUnitOfWork

UowFactory = Callable[[], AbstractAsyncContextManager[SqlAlchemyUnitOfWork]]

pytestmark = pytest.mark.usefixtures("clean_db")

_BACKENDS_DIR = Path(__file__).resolve().parents[2]
_HEAD = "0018_waiting_nodes"


def _alembic_command(verb: str, db_url: str, target: str) -> None:
    """Run an alembic command synchronously against an explicit database."""
    cfg = Config(str(_BACKENDS_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKENDS_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", normalize_sync_url(db_url))
    getattr(command, verb)(cfg, target)


def sample_graph() -> Graph:
    input_node = NodeInstance.create(
        "core.input", config={"k": "v"}, label="in", position=Position(x=10, y=-2.5)
    )
    llm_node = NodeInstance.create(
        "core.llm",
        config={
            "model": "gpt-x",
            "options": {"temperature": 0.2, "tags": ["a", "ä"], "extra": None},
        },
    )
    graph = Graph(nodes={input_node.id: input_node, llm_node.id: llm_node})
    graph.add_edge(
        Edge(
            source_node_id=input_node.id,
            source_port="out",
            target_node_id=llm_node.id,
            target_port="prompt",
        )
    )
    return graph


async def new_workflow(uow: UowFactory, name: str = "demo") -> Workflow:
    workflow = Workflow.create(name)
    async with uow() as ctx:
        await ctx.workflows.save(workflow)
        await ctx.commit()
    return workflow


async def test_workflow_roundtrip_preserves_full_draft(uow: UowFactory) -> None:
    workflow = Workflow.create("demo")
    workflow.draft.graph = sample_graph()
    workflow.draft.triggers = [Trigger.create(TriggerType.WEBHOOK, {"path": "/hook"})]
    workflow.draft.dependencies = [
        Dependency(kind="node", name="core.llm", version_constraint=">=1.0.0")
    ]
    workflow.draft.description = "first workflow, ünïcødé"
    async with uow() as ctx:
        await ctx.workflows.save(workflow)
        await ctx.commit()

    # Read through a *new* session: proves actual database round-trip, not
    # an identity-map cache hit.
    async with uow() as ctx:
        loaded = await ctx.workflows.get(workflow.id)

    assert loaded is not None
    assert loaded.id == workflow.id
    assert loaded.name == "demo"
    assert loaded.status is WorkflowStatus.DRAFT
    assert loaded.draft.description == "first workflow, ünïcødé"
    assert set(loaded.draft.graph.nodes) == set(workflow.draft.graph.nodes)
    node = loaded.draft.graph.node(next(iter(workflow.draft.graph.nodes)))
    assert node.config == workflow.draft.graph.nodes[node.id].config
    assert loaded.draft.graph.edges == workflow.draft.graph.edges
    assert {n.id: n.position for n in loaded.draft.graph.nodes.values()} == {
        n.id: n.position for n in workflow.draft.graph.nodes.values()
    }
    assert loaded.draft.triggers[0].trigger_type is TriggerType.WEBHOOK
    assert loaded.draft.triggers[0].config == {"path": "/hook"}
    assert loaded.draft.dependencies[0].version_constraint == ">=1.0.0"
    assert loaded.timestamps.created_at == workflow.timestamps.created_at


async def test_get_unknown_workflow_returns_none(uow: UowFactory) -> None:
    async with uow() as ctx:
        assert await ctx.workflows.get("wf_" + "0" * 32) is None


async def test_save_updates_existing_draft_in_place(uow: UowFactory) -> None:
    workflow = await new_workflow(uow)
    workflow.name = "renamed"
    workflow.draft.graph = sample_graph()
    async with uow() as ctx:
        await ctx.workflows.save(workflow)
        await ctx.commit()

    async with uow() as ctx:
        loaded = await ctx.workflows.get(workflow.id)
    assert loaded is not None
    assert loaded.name == "renamed"
    assert len(loaded.draft.graph.nodes) == 2


async def test_delete_workflow(uow: UowFactory) -> None:
    workflow = await new_workflow(uow)
    async with uow() as ctx:
        assert (
            await ctx.workflows.delete(workflow.id, revision=workflow.revision) is True
        )
        await ctx.commit()
    async with uow() as ctx:
        assert await ctx.workflows.get(workflow.id) is None
    async with uow() as ctx:
        assert (
            await ctx.workflows.delete(workflow.id, revision=workflow.revision) is False
        )
        await ctx.commit()


async def test_delete_with_stale_revision_is_refused(uow: UowFactory) -> None:
    workflow = await new_workflow(uow)
    async with uow() as ctx:
        assert await ctx.workflows.delete(workflow.id, revision=99) is False
        await ctx.commit()
    async with uow() as ctx:
        assert await ctx.workflows.get(workflow.id) is not None


async def test_save_is_compare_and_swap_on_revision(uow: UowFactory) -> None:
    """Two writers holding the same revision: exactly one wins (no lost
    update); the loser's changes never reach the database."""
    workflow = await new_workflow(uow)
    assert workflow.revision == 1
    async with uow() as ctx:
        first = await ctx.workflows.get(workflow.id)
    async with uow() as ctx:
        second = await ctx.workflows.get(workflow.id)
    assert first is not None and second is not None

    first.name = "first"
    async with uow() as ctx:
        await ctx.workflows.save(first)
        await ctx.commit()
    assert first.revision == 2

    second.name = "second"
    async with uow() as ctx:
        with pytest.raises(ConcurrentModificationError):
            await ctx.workflows.save(second)
    assert second.revision == 1

    async with uow() as ctx:
        loaded = await ctx.workflows.get(workflow.id)
    assert loaded is not None
    assert (loaded.name, loaded.revision) == ("first", 2)


async def test_cas_rechecks_revision_after_waiting_for_row_lock(
    uow: UowFactory, database: Database
) -> None:
    """Deterministic lost-update race: writer B blocks on A's row lock while
    holding the same revision. After A commits, PostgreSQL re-evaluates B's
    WHERE clause and B must lose — not overwrite A."""
    workflow = await new_workflow(uow)
    async with uow() as a, uow() as b:
        pa = await a.workflows.get(workflow.id)
        pb = await b.workflows.get(workflow.id)
        assert pa is not None and pb is not None
        pa.name, pb.name = "a", "b"
        await a.workflows.save(pa)  # row now locked by A's open transaction
        blocked = asyncio.create_task(b.workflows.save(pb))

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

        # Bounded polling on external database state (an Event cannot
        # observe PostgreSQL's lock table); never a blind sleep.
        for _ in range(1000):
            if await lock_waiters():
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("writer B never blocked on writer A's row lock")
        assert not blocked.done()
        await a.commit()
        with pytest.raises(ConcurrentModificationError):
            await blocked

    async with uow() as ctx:
        loaded = await ctx.workflows.get(workflow.id)
    assert loaded is not None and (loaded.name, loaded.revision) == ("a", 2)


async def test_insert_of_existing_id_is_a_conflict_not_an_overwrite(
    uow: UowFactory,
) -> None:
    workflow = await new_workflow(uow, "original")
    duplicate = Workflow(id=workflow.id, name="impostor")  # revision 0 = new
    async with uow() as ctx:
        with pytest.raises(ConcurrentModificationError):
            await ctx.workflows.save(duplicate)
    async with uow() as ctx:
        loaded = await ctx.workflows.get(workflow.id)
    assert loaded is not None and loaded.name == "original"


async def test_version_counter_round_trips(uow: UowFactory) -> None:
    workflow = await new_workflow(uow)
    snapshot = workflow.publish_draft()
    async with uow() as ctx:
        await ctx.workflows.save(workflow)
        await ctx.versions.save(workflow.id, snapshot)
        await ctx.commit()
    async with uow() as ctx:
        loaded = await ctx.workflows.get(workflow.id)
    assert loaded is not None
    assert loaded.current_version == 1
    assert loaded.status is WorkflowStatus.PUBLISHED


async def test_list_workflows_is_paginated_and_ordered(uow: UowFactory) -> None:
    workflows = [await new_workflow(uow, f"p{i}") for i in range(3)]
    async with uow() as ctx:
        first = await ctx.workflows.list(offset=0, limit=2)
        second = await ctx.workflows.list(offset=2, limit=2)
    assert [p.id for p in first] + [p.id for p in second] == [p.id for p in workflows]


async def test_publish_snapshot_is_isolated_from_later_draft_edits(
    uow: UowFactory,
) -> None:
    """Draft edits after publish must never retro-change a stored version
    (architecture §3.7)."""
    workflow = Workflow.create("demo")
    workflow.draft.graph = sample_graph()
    snapshot = workflow.publish_draft()

    async with uow() as ctx:
        await ctx.workflows.save(workflow)
        await ctx.versions.save(workflow.id, snapshot)
        await ctx.commit()

    # Mutate the draft *after* publishing; a snapshot stays a snapshot.
    workflow.draft.description = "edited after publish"
    workflow.draft.graph = Graph()
    async with uow() as ctx:
        await ctx.workflows.save(workflow)
        await ctx.commit()

    async with uow() as ctx:
        loaded_version = await ctx.versions.get(workflow.id, 1)
    assert loaded_version is not None
    assert loaded_version.version == 1
    assert loaded_version.schema_version == 1
    assert len(loaded_version.graph.nodes) == 2  # not the emptied draft
    assert loaded_version.description == snapshot.description
    assert loaded_version.published_at == snapshot.published_at
    assert loaded_version.published_at.tzinfo is not None


async def test_version_listing_and_lookup(uow: UowFactory) -> None:
    workflow = await new_workflow(uow)
    first = workflow.publish_draft()
    second = workflow.publish_draft()
    async with uow() as ctx:
        await ctx.workflows.save(workflow)
        await ctx.versions.save(workflow.id, first)
        await ctx.versions.save(workflow.id, second)
        await ctx.commit()

    async with uow() as ctx:
        versions = await ctx.versions.list(workflow.id)
    assert [v.version for v in versions] == [1, 2]
    async with uow() as ctx:
        assert await ctx.versions.get(workflow.id, 99) is None


async def test_duplicate_version_number_rejected_by_unique_constraint(
    uow: UowFactory,
) -> None:
    """The database is the last line of defence for version immutability:
    a second version row with the same (workflow_id, version) — regardless of
    primary key — violates the unique constraint."""
    workflow = await new_workflow(uow)
    one = workflow.publish_draft()
    async with uow() as ctx:
        await ctx.versions.save(workflow.id, one)
        await ctx.commit()

    conflicting = WorkflowVersion(
        id=new_id("wfver"),  # different primary key, same logical version
        version=1,
        schema_version=one.schema_version,
        graph=Graph(),
        published_at=utcnow(),
    )
    async with uow() as ctx:
        with pytest.raises(IntegrityError):  # asyncpg raises at INSERT
            await ctx.versions.save(workflow.id, conflicting)


async def test_version_without_workflow_violates_foreign_key(
    uow: UowFactory,
) -> None:
    workflow = Workflow.create("orphan")
    snapshot = workflow.publish_draft()
    async with uow() as ctx:
        with pytest.raises(IntegrityError):
            await ctx.versions.save(workflow.id, snapshot)  # workflow never saved


async def test_workflow_delete_blocked_while_versions_exist(uow: UowFactory) -> None:
    """ondelete=RESTRICT: version history cannot be silently destroyed."""
    workflow = Workflow.create("demo")
    snapshot = workflow.publish_draft()
    async with uow() as ctx:
        await ctx.workflows.save(workflow)
        await ctx.versions.save(workflow.id, snapshot)
        await ctx.commit()

    async with uow() as ctx:
        with pytest.raises(IntegrityError):
            await ctx.workflows.delete(workflow.id, revision=workflow.revision)

    async with uow() as ctx:
        assert await ctx.workflows.get(workflow.id) is not None


async def test_uncommitted_work_is_rolled_back(uow: UowFactory) -> None:
    """Leaving the unit of work with an exception discards everything (§16)."""
    workflow = Workflow.create("demo")
    with pytest.raises(RuntimeError):
        async with uow() as ctx:
            await ctx.workflows.save(workflow)
            raise RuntimeError("boom")
    async with uow() as ctx:
        assert await ctx.workflows.get(workflow.id) is None


async def test_readiness_check_against_real_database(database: Database) -> None:
    assert await database.check() is None


async def test_readiness_check_reports_unreachable_database() -> None:
    db = Database("postgresql+asyncpg://127.0.0.1:1/akasha")
    try:
        reason = await db.check()
    finally:
        await db.dispose()
    assert reason is not None and "unreachable" in reason


async def test_readiness_requires_the_schema_at_head(db_url: str) -> None:
    """A new engine on an unmigrated database must not report ready (Phase
    26): the API serves only once ``akasha migrate`` has run."""
    admin = Database(normalize_async_url(db_url))
    scratch = "akasha_readiness_schema"
    async with admin.engine.connect() as conn:
        await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(text(f"DROP DATABASE IF EXISTS {scratch}"))
        await conn.execute(text(f"CREATE DATABASE {scratch}"))
    scratch_url = f"{db_url.rpartition('/')[0]}/{scratch}"
    db = Database(normalize_async_url(scratch_url))
    try:
        assert await db.schema_revision() is None
        reason = await db.check_schema()
        assert reason is not None and "akasha migrate" in reason
        await asyncio.to_thread(
            _alembic_command, "upgrade", scratch_url, "0009_queued_executions_index"
        )
        reason = await db.check_schema()
        assert reason is not None and "0009_queued_executions_index" in reason

        await db.run_migrations(scratch_url)
        assert await db.schema_revision() == _HEAD
        assert await db.check_schema() is None
    finally:
        await db.dispose()
        async with admin.engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text(f"DROP DATABASE IF EXISTS {scratch}"))
        await admin.dispose()


async def test_migrations_upgrade_downgrade_upgrade_on_empty_database(
    db_url: str,
) -> None:
    """A clean database must reach `head` deterministically, and the schema
    must be reversible (backend/AGENTS.md §18). Runs in a dedicated scratch
    database inside the session container."""
    admin = Database(normalize_async_url(db_url))
    scratch = "akasha_migration_check"
    try:
        # CREATE DATABASE cannot run inside a transaction.
        async with admin.engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text(f"DROP DATABASE IF EXISTS {scratch}"))
            await conn.execute(text(f"CREATE DATABASE {scratch}"))

        base = db_url.rpartition("/")[0]  # credentials may contain the db name
        scratch_url = f"{base}/{scratch}"
        await asyncio.to_thread(_alembic_command, "upgrade", scratch_url, "head")

        verify = Database(normalize_async_url(scratch_url))
        try:
            async with verify.engine.connect() as conn:
                tables = {
                    row[0]
                    for row in await conn.execute(
                        text(
                            "SELECT tablename FROM pg_tables "
                            "WHERE schemaname = 'public'"
                        )
                    )
                }
                rev: str = (
                    await conn.execute(text("SELECT version_num FROM alembic_version"))
                ).scalar_one()
            assert {
                "workflows",
                "workflow_versions",
                "executions",
                "node_executions",
                "events",
                "credentials",
                "users",
                "sessions",
                "workflow_members",
            } <= tables
            assert rev == _HEAD

            await asyncio.to_thread(_alembic_command, "downgrade", scratch_url, "base")
            async with verify.engine.connect() as conn:
                tables = {
                    row[0]
                    for row in await conn.execute(
                        text(
                            "SELECT tablename FROM pg_tables "
                            "WHERE schemaname = 'public'"
                        )
                    )
                }
            assert "workflows" not in tables
            assert "workflow_versions" not in tables
            assert "executions" not in tables
            assert "node_executions" not in tables
            assert "events" not in tables
            assert "credentials" not in tables
            assert "users" not in tables
            assert "sessions" not in tables
            assert "workflow_members" not in tables

            await asyncio.to_thread(_alembic_command, "upgrade", scratch_url, "head")
            async with verify.engine.connect() as conn:
                rev = (
                    await conn.execute(text("SELECT version_num FROM alembic_version"))
                ).scalar_one()
            assert rev == _HEAD
        finally:
            await verify.dispose()
    finally:
        async with admin.engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text(f"DROP DATABASE IF EXISTS {scratch}"))
        await admin.dispose()


async def test_0002_upgrade_backfills_existing_workflows(db_url: str) -> None:
    """Previous supported schema + real rows → new migration → application
    works (AGENTS §18): counters are backfilled from existing versions and
    the repository can CAS-update the upgraded row."""
    admin = Database(normalize_async_url(db_url))
    scratch = "akasha_migration_0002"
    try:
        async with admin.engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text(f"DROP DATABASE IF EXISTS {scratch}"))
            await conn.execute(text(f"CREATE DATABASE {scratch}"))
        scratch_url = f"{db_url.rpartition('/')[0]}/{scratch}"
        await asyncio.to_thread(
            _alembic_command, "upgrade", scratch_url, "0001_initial"
        )

        published, unpublished = new_id("wf"), new_id("wf")
        db = Database(normalize_async_url(scratch_url))
        try:
            async with db.engine.begin() as conn:
                for pid in (published, unpublished):
                    await conn.execute(
                        text(
                            "INSERT INTO workflows (id, name, status, draft_graph,"
                            " draft_triggers, draft_dependencies, created_at,"
                            " updated_at) VALUES (:id, 'p', 'draft',"
                            " '{\"nodes\": [], \"edges\": []}', '[]', '[]',"
                            " now(), now())"
                        ),
                        {"id": pid},
                    )
                for number in (1, 2):
                    await conn.execute(
                        text(
                            "INSERT INTO workflow_versions (id, workflow_id, version,"
                            " schema_version, graph, triggers, dependencies,"
                            " published_at) VALUES (:id, :pid, :v, 1,"
                            " '{\"nodes\": [], \"edges\": []}', '[]', '[]', now())"
                        ),
                        {"id": new_id("wfver"), "pid": published, "v": number},
                    )
            await asyncio.to_thread(_alembic_command, "upgrade", scratch_url, "head")

            async with SqlAlchemyUnitOfWork(db) as ctx:
                old = await ctx.workflows.get(published)
                fresh = await ctx.workflows.get(unpublished)
            assert old is not None and fresh is not None
            assert (old.current_version, old.revision) == (2, 1)
            assert (fresh.current_version, fresh.revision) == (0, 1)

            old.name = "renamed after upgrade"
            async with SqlAlchemyUnitOfWork(db) as ctx:
                await ctx.workflows.save(old)
                await ctx.commit()
            assert old.revision == 2
        finally:
            await db.dispose()
    finally:
        async with admin.engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text(f"DROP DATABASE IF EXISTS {scratch}"))
        await admin.dispose()


async def test_0007_upgrade_gives_existing_workflows_and_versions_no_files(
    db_url: str,
) -> None:
    """Rows written under 0006 load after 0007 with empty files, and the
    upgraded row can store files and publish a version carrying them."""
    admin = Database(normalize_async_url(db_url))
    scratch = "akasha_migration_0007"
    try:
        async with admin.engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text(f"DROP DATABASE IF EXISTS {scratch}"))
            await conn.execute(text(f"CREATE DATABASE {scratch}"))
        scratch_url = f"{db_url.rpartition('/')[0]}/{scratch}"
        await asyncio.to_thread(
            _alembic_command, "upgrade", scratch_url, "0006_credentials"
        )
        pid = new_id("wf")
        db = Database(normalize_async_url(scratch_url))
        try:
            async with db.engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO workflows (id, name, status, draft_graph,"
                        " draft_triggers, draft_dependencies, current_version,"
                        " created_at, updated_at) VALUES (:id, 'p', 'published',"
                        " '{\"nodes\": [], \"edges\": []}', '[]', '[]', 1,"
                        " now(), now())"
                    ),
                    {"id": pid},
                )
                await conn.execute(
                    text(
                        "INSERT INTO workflow_versions (id, workflow_id, version,"
                        " schema_version, graph, triggers, dependencies,"
                        " published_at) VALUES (:id, :pid, 1, 1,"
                        " '{\"nodes\": [], \"edges\": []}', '[]', '[]', now())"
                    ),
                    {"id": new_id("wfver"), "pid": pid},
                )
            await asyncio.to_thread(_alembic_command, "upgrade", scratch_url, "head")

            async with SqlAlchemyUnitOfWork(db) as ctx:
                workflow = await ctx.workflows.get(pid)
                old_version = await ctx.versions.get(pid, 1)
            assert workflow is not None and old_version is not None
            assert workflow.draft.files == {} and old_version.files == {}

            workflow.replace_draft(
                replace(workflow.draft, files={"docs/a.md": b"after upgrade"}),
                name="p",
            )
            snapshot = workflow.publish_draft()
            async with SqlAlchemyUnitOfWork(db) as ctx:
                await ctx.workflows.save(workflow)
                await ctx.versions.save(pid, snapshot)
                await ctx.commit()
            async with SqlAlchemyUnitOfWork(db) as ctx:
                stored = await ctx.versions.get(pid, 2)
            assert stored is not None
            assert stored.files == {"docs/a.md": b"after upgrade"}
        finally:
            await db.dispose()
    finally:
        async with admin.engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text(f"DROP DATABASE IF EXISTS {scratch}"))
        await admin.dispose()


async def test_0008_upgrade_leaves_existing_workflows_ownerless(db_url: str) -> None:
    """Workflows written under 0007 load after 0008 with no owner (admin-only,
    so upgrading never widens access); owners must be real users."""
    admin = Database(normalize_async_url(db_url))
    scratch = "akasha_migration_0008"
    try:
        async with admin.engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text(f"DROP DATABASE IF EXISTS {scratch}"))
            await conn.execute(text(f"CREATE DATABASE {scratch}"))
        scratch_url = f"{db_url.rpartition('/')[0]}/{scratch}"
        await asyncio.to_thread(
            _alembic_command, "upgrade", scratch_url, "0007_workflow_files"
        )
        pid = new_id("wf")
        db = Database(normalize_async_url(scratch_url))
        try:
            async with db.engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO workflows (id, name, status, draft_graph,"
                        " draft_triggers, draft_dependencies, created_at,"
                        " updated_at) VALUES (:id, 'p', 'draft',"
                        " '{\"nodes\": [], \"edges\": []}', '[]', '[]',"
                        " now(), now())"
                    ),
                    {"id": pid},
                )
            await asyncio.to_thread(_alembic_command, "upgrade", scratch_url, "head")

            async with SqlAlchemyUnitOfWork(db) as ctx:
                workflow = await ctx.workflows.get(pid)
            assert workflow is not None and workflow.owner_id is None

            orphan = Workflow.create("orphan", owner_id=new_id("user"))
            with pytest.raises(IntegrityError):
                async with SqlAlchemyUnitOfWork(db) as ctx:
                    await ctx.workflows.save(orphan)
                    await ctx.commit()
        finally:
            await db.dispose()
    finally:
        async with admin.engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text(f"DROP DATABASE IF EXISTS {scratch}"))
        await admin.dispose()


async def test_0011_upgrade_leaves_existing_versions_without_author(
    db_url: str,
) -> None:
    """Versions written under 0010 load after 0011 with no author (never
    guessed); a recorded author must be a real user."""
    admin = Database(normalize_async_url(db_url))
    scratch = "akasha_migration_0011"
    try:
        async with admin.engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text(f"DROP DATABASE IF EXISTS {scratch}"))
            await conn.execute(text(f"CREATE DATABASE {scratch}"))
        scratch_url = f"{db_url.rpartition('/')[0]}/{scratch}"
        await asyncio.to_thread(
            _alembic_command, "upgrade", scratch_url, "0010_execution_heartbeat"
        )
        pid = new_id("wf")
        db = Database(normalize_async_url(scratch_url))
        try:
            async with db.engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO workflows (id, name, status, draft_graph,"
                        " draft_triggers, draft_dependencies, current_version,"
                        " created_at, updated_at) VALUES (:id, 'p', 'published',"
                        " '{\"nodes\": [], \"edges\": []}', '[]', '[]', 1,"
                        " now(), now())"
                    ),
                    {"id": pid},
                )
                await conn.execute(
                    text(
                        "INSERT INTO workflow_versions (id, workflow_id, version,"
                        " schema_version, graph, triggers, dependencies,"
                        " published_at) VALUES (:id, :pid, 1, 1,"
                        " '{\"nodes\": [], \"edges\": []}', '[]', '[]', now())"
                    ),
                    {"id": new_id("wfver"), "pid": pid},
                )
            await asyncio.to_thread(_alembic_command, "upgrade", scratch_url, "head")

            async with SqlAlchemyUnitOfWork(db) as ctx:
                workflow = await ctx.workflows.get(pid)
                old_version = await ctx.versions.get(pid, 1)
            assert workflow is not None and old_version is not None
            assert old_version.published_by is None

            snapshot = workflow.publish_draft(published_by=new_id("user"))
            with pytest.raises(IntegrityError):
                async with SqlAlchemyUnitOfWork(db) as ctx:
                    await ctx.workflows.save(workflow)
                    await ctx.versions.save(pid, snapshot)
                    await ctx.commit()
        finally:
            await db.dispose()
    finally:
        async with admin.engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text(f"DROP DATABASE IF EXISTS {scratch}"))
        await admin.dispose()


async def test_0018_upgrade_keeps_executions_and_downgrade_refuses_waiting_nodes(
    db_url: str,
) -> None:
    """Executions written under 0017 load after 0018 as "not waiting"; a
    downgrade with a waiting node is refused instead of losing its state."""
    admin = Database(normalize_async_url(db_url))
    scratch = "akasha_migration_0018"
    try:
        async with admin.engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text(f"DROP DATABASE IF EXISTS {scratch}"))
            await conn.execute(text(f"CREATE DATABASE {scratch}"))
        scratch_url = f"{db_url.rpartition('/')[0]}/{scratch}"
        await asyncio.to_thread(
            _alembic_command, "upgrade", scratch_url, "0017_workflow_members"
        )
        wf, node, exe, nexec = (
            new_id("wf"),
            new_id("node"),
            new_id("exec"),
            new_id("nexec"),
        )
        graph = json.dumps(
            {
                "nodes": [
                    {"id": node, "node_type": "core.output", "config": {}, "label": ""}
                ],
                "edges": [],
            }
        )
        db = Database(normalize_async_url(scratch_url))
        try:
            async with db.engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO workflows (id, name, status, draft_graph,"
                        " draft_triggers, draft_dependencies, current_version,"
                        " created_at, updated_at) VALUES (:id, 'p', 'published',"
                        " CAST(:g AS jsonb), '[]', '[]', 1, now(), now())"
                    ),
                    {"id": wf, "g": graph},
                )
                await conn.execute(
                    text(
                        "INSERT INTO workflow_versions (id, workflow_id, version,"
                        " schema_version, graph, triggers, dependencies,"
                        " published_at) VALUES (:id, :wf, 1, 1, CAST(:g AS jsonb),"
                        " '[]', '[]', now())"
                    ),
                    {"id": new_id("wfver"), "wf": wf, "g": graph},
                )
                await conn.execute(
                    text(
                        "INSERT INTO executions (id, workflow_id, workflow_version,"
                        " status, metadata, retry_policy, timeout_policy, revision,"
                        " created_at, started_at, finished_at) VALUES (:id, :wf, 1,"
                        " 'completed', CAST(:meta AS jsonb), CAST(:retry AS jsonb),"
                        " CAST(:timeout AS jsonb), 3, now(), now(), now())"
                    ),
                    {
                        "id": exe,
                        "wf": wf,
                        "meta": '{"trigger_type": "manual", "trigger_id": null,'
                        ' "correlation_id": null, "engine_version": "0.1.0"}',
                        "retry": '{"max_attempts": 1, "initial_backoff_s": 1,'
                        ' "backoff_multiplier": 2, "max_backoff_s": 60,'
                        ' "retryable_kinds": ["timeout", "transient"]}',
                        "timeout": '{"execution_timeout_s": 3600,'
                        ' "node_timeout_s": 900}',
                    },
                )
                await conn.execute(
                    text(
                        "INSERT INTO node_executions (id, execution_id, node_id,"
                        " status, attempt, failures, outputs, started_at,"
                        " finished_at) VALUES (:id, :exe, :node, 'completed', 1,"
                        " '[]', '{\"value\": 1}', now(), now())"
                    ),
                    {"id": nexec, "exe": exe, "node": node},
                )
            await asyncio.to_thread(_alembic_command, "upgrade", scratch_url, "head")

            async with SqlAlchemyUnitOfWork(db) as ctx:
                execution = await ctx.executions.get(exe)
            assert execution is not None and execution.wake_at is None
            loaded = execution.node(node)
            assert loaded.outputs == {"value": 1}
            assert (loaded.waiting_for, loaded.resume_input) == (None, None)

            async with db.engine.begin() as conn:
                await conn.execute(
                    text(
                        "UPDATE node_executions SET status = 'waiting',"
                        ' wait = \'{"prompt": "ok?", "response_schema":'
                        ' {"type": "object"}, "deadline": null}\''
                    )
                )
            with pytest.raises(RuntimeError, match="node executions are waiting"):
                await asyncio.to_thread(
                    _alembic_command, "downgrade", scratch_url, "0017_workflow_members"
                )
        finally:
            await db.dispose()
    finally:
        async with admin.engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text(f"DROP DATABASE IF EXISTS {scratch}"))
        await admin.dispose()
