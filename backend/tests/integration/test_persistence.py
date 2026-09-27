"""Integration tests for the persistence foundation against real PostgreSQL.

Everything here exercises the actual database: migrations, repository CRUD,
transaction boundaries, constraints, JSONB round-trip fidelity and immutable
published versions. No mocks for persistence behavior (backend/AGENTS.md
§54/§61).
"""

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from akasha.domain.graph import Edge, Graph, NodeInstance
from akasha.domain.primitives import new_id, utcnow
from akasha.domain.project import Dependency, Project, ProjectStatus, ProjectVersion
from akasha.domain.trigger import Trigger, TriggerType
from akasha.infrastructure.database import (
    Database,
    normalize_async_url,
    normalize_sync_url,
)
from akasha.infrastructure.persistence import SqlAlchemyUnitOfWork

UowFactory = Callable[[], AbstractAsyncContextManager[SqlAlchemyUnitOfWork]]

pytestmark = pytest.mark.usefixtures("clean_db")

_BACKENDS_DIR = Path(__file__).resolve().parents[2]


def _alembic_command(verb: str, db_url: str, target: str) -> None:
    """Run an alembic command synchronously against an explicit database."""
    cfg = Config(str(_BACKENDS_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKENDS_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", normalize_sync_url(db_url))
    getattr(command, verb)(cfg, target)


def sample_graph() -> Graph:
    input_node = NodeInstance.create("core.input", config={"k": "v"}, label="in")
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


async def new_project(uow: UowFactory, name: str = "demo") -> Project:
    project = Project.create(name)
    async with uow() as ctx:
        await ctx.projects.save(project)
        await ctx.commit()
    return project


async def test_project_roundtrip_preserves_full_draft(uow: UowFactory) -> None:
    project = Project.create("demo")
    project.draft.graph = sample_graph()
    project.draft.triggers = [Trigger.create(TriggerType.WEBHOOK, {"path": "/hook"})]
    project.draft.dependencies = [
        Dependency(kind="node", name="core.llm", version_constraint=">=1.0.0")
    ]
    project.draft.description = "first project, ünïcødé"
    async with uow() as ctx:
        await ctx.projects.save(project)
        await ctx.commit()

    # Read through a *new* session: proves actual database round-trip, not
    # an identity-map cache hit.
    async with uow() as ctx:
        loaded = await ctx.projects.get(project.id)

    assert loaded is not None
    assert loaded.id == project.id
    assert loaded.name == "demo"
    assert loaded.status is ProjectStatus.DRAFT
    assert loaded.draft.description == "first project, ünïcødé"
    assert set(loaded.draft.graph.nodes) == set(project.draft.graph.nodes)
    node = loaded.draft.graph.node(next(iter(project.draft.graph.nodes)))
    assert node.config == project.draft.graph.nodes[node.id].config
    assert loaded.draft.graph.edges == project.draft.graph.edges
    assert loaded.draft.triggers[0].trigger_type is TriggerType.WEBHOOK
    assert loaded.draft.triggers[0].config == {"path": "/hook"}
    assert loaded.draft.dependencies[0].version_constraint == ">=1.0.0"
    assert loaded.timestamps.created_at == project.timestamps.created_at


async def test_get_unknown_project_returns_none(uow: UowFactory) -> None:
    async with uow() as ctx:
        assert await ctx.projects.get("proj_" + "0" * 32) is None


async def test_save_updates_existing_draft_in_place(uow: UowFactory) -> None:
    project = await new_project(uow)
    project.name = "renamed"
    project.draft.graph = sample_graph()
    async with uow() as ctx:
        await ctx.projects.save(project)
        await ctx.commit()

    async with uow() as ctx:
        loaded = await ctx.projects.get(project.id)
    assert loaded is not None
    assert loaded.name == "renamed"
    assert len(loaded.draft.graph.nodes) == 2


async def test_delete_project(uow: UowFactory) -> None:
    project = await new_project(uow)
    async with uow() as ctx:
        assert await ctx.projects.delete(project.id) is True
        await ctx.commit()
    async with uow() as ctx:
        assert await ctx.projects.get(project.id) is None
    async with uow() as ctx:
        assert await ctx.projects.delete(project.id) is False
        await ctx.commit()


async def test_list_projects_is_paginated_and_ordered(uow: UowFactory) -> None:
    projects = [await new_project(uow, f"p{i}") for i in range(3)]
    async with uow() as ctx:
        first = await ctx.projects.list(offset=0, limit=2)
        second = await ctx.projects.list(offset=2, limit=2)
    assert [p.id for p in first] + [p.id for p in second] == [p.id for p in projects]


async def test_publish_snapshot_is_isolated_from_later_draft_edits(
    uow: UowFactory,
) -> None:
    """Draft edits after publish must never retro-change a stored version
    (architecture §3.7)."""
    project = Project.create("demo")
    project.draft.graph = sample_graph()
    snapshot = project.publish_draft()

    async with uow() as ctx:
        await ctx.projects.save(project)
        await ctx.versions.save(project.id, snapshot)
        await ctx.commit()

    # Mutate the draft *after* publishing; a snapshot stays a snapshot.
    project.draft.description = "edited after publish"
    project.draft.graph = Graph()
    async with uow() as ctx:
        await ctx.projects.save(project)
        await ctx.commit()

    async with uow() as ctx:
        loaded_version = await ctx.versions.get(project.id, 1)
    assert loaded_version is not None
    assert loaded_version.version == 1
    assert loaded_version.schema_version == 1
    assert len(loaded_version.graph.nodes) == 2  # not the emptied draft
    assert loaded_version.description == snapshot.description
    assert loaded_version.published_at == snapshot.published_at
    assert loaded_version.published_at.tzinfo is not None


async def test_version_listing_and_lookup(uow: UowFactory) -> None:
    project = await new_project(uow)
    first = project.publish_draft()
    second = project.publish_draft()
    async with uow() as ctx:
        await ctx.projects.save(project)
        await ctx.versions.save(project.id, first)
        await ctx.versions.save(project.id, second)
        await ctx.commit()

    async with uow() as ctx:
        versions = await ctx.versions.list(project.id)
    assert [v.version for v in versions] == [1, 2]
    async with uow() as ctx:
        assert await ctx.versions.get(project.id, 99) is None


async def test_duplicate_version_number_rejected_by_unique_constraint(
    uow: UowFactory,
) -> None:
    """The database is the last line of defence for version immutability:
    a second version row with the same (project_id, version) — regardless of
    primary key — violates the unique constraint."""
    project = await new_project(uow)
    one = project.publish_draft()
    async with uow() as ctx:
        await ctx.versions.save(project.id, one)
        await ctx.commit()

    conflicting = ProjectVersion(
        id=new_id("pver"),  # different primary key, same logical version
        version=1,
        schema_version=one.schema_version,
        graph=Graph(),
        published_at=utcnow(),
    )
    async with uow() as ctx:
        with pytest.raises(IntegrityError):  # asyncpg raises at INSERT
            await ctx.versions.save(project.id, conflicting)


async def test_version_without_project_violates_foreign_key(
    uow: UowFactory,
) -> None:
    project = Project.create("orphan")
    snapshot = project.publish_draft()
    async with uow() as ctx:
        with pytest.raises(IntegrityError):
            await ctx.versions.save(project.id, snapshot)  # project never saved


async def test_project_delete_blocked_while_versions_exist(uow: UowFactory) -> None:
    """ondelete=RESTRICT: version history cannot be silently destroyed."""
    project = Project.create("demo")
    snapshot = project.publish_draft()
    async with uow() as ctx:
        await ctx.projects.save(project)
        await ctx.versions.save(project.id, snapshot)
        await ctx.commit()

    async with uow() as ctx:
        with pytest.raises(IntegrityError):
            await ctx.projects.delete(project.id)

    async with uow() as ctx:
        assert await ctx.projects.get(project.id) is not None


async def test_uncommitted_work_is_rolled_back(uow: UowFactory) -> None:
    """Leaving the unit of work with an exception discards everything (§16)."""
    project = Project.create("demo")
    with pytest.raises(RuntimeError):
        async with uow() as ctx:
            await ctx.projects.save(project)
            raise RuntimeError("boom")
    async with uow() as ctx:
        assert await ctx.projects.get(project.id) is None


async def test_readiness_check_against_real_database(database: Database) -> None:
    assert await database.check() is None


async def test_readiness_check_reports_unreachable_database() -> None:
    db = Database("postgresql+asyncpg://127.0.0.1:1/akasha")
    try:
        reason = await db.check()
    finally:
        await db.dispose()
    assert reason is not None and "unreachable" in reason


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
            assert {"projects", "project_versions"} <= tables
            assert rev == "0001_initial"

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
            assert "projects" not in tables
            assert "project_versions" not in tables

            await asyncio.to_thread(_alembic_command, "upgrade", scratch_url, "head")
            async with verify.engine.connect() as conn:
                rev = (
                    await conn.execute(text("SELECT version_num FROM alembic_version"))
                ).scalar_one()
            assert rev == "0001_initial"
        finally:
            await verify.dispose()
    finally:
        async with admin.engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text(f"DROP DATABASE IF EXISTS {scratch}"))
        await admin.dispose()
