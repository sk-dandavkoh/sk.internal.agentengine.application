"""WorkflowService against real PostgreSQL (roadmap Phase 06).

Exercises the use cases through the real Unit of Work: lifecycle, version
immutability, optimistic concurrency under actual row locks, and publish
atomicity. No persistence mocks (backend/AGENTS.md §54/§61).
"""

import asyncio

import pytest

from akasha.application.executions import ExecutionService
from akasha.application.persistence import ConcurrentModificationError
from akasha.application.workflows import (
    WorkflowArchivedError,
    WorkflowHasVersionsError,
    WorkflowNotFoundError,
    WorkflowService,
    WorkflowValidationFailedError,
    WorkflowVersionNotFoundError,
)
from akasha.domain.graph import Edge, Graph, NodeInstance
from akasha.domain.graph_validation import ValidationCode
from akasha.domain.node import NodeDefinition
from akasha.domain.workflow import WorkflowDraft, WorkflowStatus, WorkflowVersion
from akasha.errors import DomainValidationError
from akasha.infrastructure.database import Database
from akasha.infrastructure.persistence import (
    SqlAlchemyUnitOfWork,
    SqlAlchemyWorkflowVersionRepository,
)

pytestmark = pytest.mark.usefixtures("clean_db")


def valid_draft(description: str = "") -> WorkflowDraft:
    source = NodeInstance.create("test.source")
    sink = NodeInstance.create("test.sink", config={"prefix": ">"})
    graph = Graph()
    graph.add_node(source)
    graph.add_node(sink)
    graph.add_edge(Edge(source.id, "text", sink.id, "text"))
    return WorkflowDraft(graph=graph, description=description)


async def published_workflow(svc: WorkflowService) -> tuple[str, WorkflowVersion]:
    workflow = await svc.create_workflow("demo")
    await svc.update_draft(
        workflow.id, expected_revision=1, name="demo", draft=valid_draft("v1")
    )
    return workflow.id, await svc.publish_workflow(workflow.id)


async def test_create_and_get_round_trip(workflow_service: WorkflowService) -> None:
    created = await workflow_service.create_workflow("demo", description="about")
    loaded = await workflow_service.get_workflow(created.id)
    assert (loaded.name, loaded.draft.description) == ("demo", "about")
    assert (loaded.revision, loaded.current_version) == (1, 0)
    assert loaded.status is WorkflowStatus.DRAFT


async def test_create_rejects_blank_name(workflow_service: WorkflowService) -> None:
    with pytest.raises(DomainValidationError):
        await workflow_service.create_workflow("  ")


async def test_get_unknown_workflow(workflow_service: WorkflowService) -> None:
    with pytest.raises(WorkflowNotFoundError):
        await workflow_service.get_workflow("wf_" + "0" * 32)


async def test_list_is_paginated(workflow_service: WorkflowService) -> None:
    ids = [(await workflow_service.create_workflow(f"p{i}")).id for i in range(3)]
    first = await workflow_service.list_workflows(offset=0, limit=2)
    rest = await workflow_service.list_workflows(offset=2, limit=2)
    assert [p.id for p in first.items] == ids[:2] and first.has_more
    assert [p.id for p in rest.items] == ids[2:] and not rest.has_more


@pytest.mark.parametrize(("offset", "limit"), [(-1, 10), (0, 0), (0, 101)])
async def test_list_rejects_bad_page(
    workflow_service: WorkflowService, offset: int, limit: int
) -> None:
    with pytest.raises(DomainValidationError):
        await workflow_service.list_workflows(offset=offset, limit=limit)


async def test_update_draft_persists_and_advances_revision(
    workflow_service: WorkflowService,
) -> None:
    workflow = await workflow_service.create_workflow("demo")
    draft = valid_draft("edited")
    updated = await workflow_service.update_draft(
        workflow.id, expected_revision=1, name="renamed", draft=draft
    )
    assert updated.revision == 2
    loaded = await workflow_service.get_workflow(workflow.id)
    assert (loaded.name, loaded.revision) == ("renamed", 2)
    assert set(loaded.draft.graph.nodes) == set(draft.graph.nodes)
    assert loaded.draft.description == "edited"


async def test_update_draft_accepts_semantically_invalid_draft(
    workflow_service: WorkflowService,
) -> None:
    """A draft is work in progress; only publish demands validity."""
    workflow = await workflow_service.create_workflow("demo")
    graph = Graph()
    graph.add_node(NodeInstance.create("no.such_node"))
    await workflow_service.update_draft(
        workflow.id, expected_revision=1, name="demo", draft=WorkflowDraft(graph=graph)
    )
    result = await workflow_service.validate_workflow(workflow.id)
    codes = {i.code for i in result.issues}
    assert ValidationCode.UNKNOWN_NODE_TYPE in codes


async def test_stale_revision_is_rejected_without_writing(
    workflow_service: WorkflowService,
) -> None:
    workflow = await workflow_service.create_workflow("demo")
    await workflow_service.update_draft(
        workflow.id, expected_revision=1, name="v2", draft=WorkflowDraft()
    )
    with pytest.raises(ConcurrentModificationError) as exc:
        await workflow_service.update_draft(
            workflow.id, expected_revision=1, name="stale", draft=WorkflowDraft()
        )
    assert exc.value.details["current_revision"] == 2
    assert (await workflow_service.get_workflow(workflow.id)).name == "v2"


async def test_concurrent_updates_with_same_revision_one_wins(
    workflow_service: WorkflowService,
) -> None:
    """Whatever the interleaving (in-memory revision check or database CAS
    after a row-lock wait — the latter is pinned deterministically in
    test_persistence), exactly one writer commits."""
    workflow = await workflow_service.create_workflow("demo")
    results = await asyncio.gather(
        *(
            workflow_service.update_draft(
                workflow.id,
                expected_revision=1,
                name=f"writer-{i}",
                draft=WorkflowDraft(),
            )
            for i in range(2)
        ),
        return_exceptions=True,
    )
    conflicts = [r for r in results if isinstance(r, ConcurrentModificationError)]
    assert len(conflicts) == 1
    loaded = await workflow_service.get_workflow(workflow.id)
    assert loaded.revision == 2
    assert loaded.name in {"writer-0", "writer-1"}


async def test_validate_valid_and_invalid_drafts(
    workflow_service: WorkflowService,
) -> None:
    workflow = await workflow_service.create_workflow("demo")
    empty = await workflow_service.validate_workflow(workflow.id)
    assert [i.code for i in empty.issues] == [ValidationCode.EMPTY_GRAPH]

    await workflow_service.update_draft(
        workflow.id, expected_revision=1, name="demo", draft=valid_draft()
    )
    assert (await workflow_service.validate_workflow(workflow.id)).ok


async def test_publish_invalid_draft_persists_nothing(
    workflow_service: WorkflowService,
) -> None:
    workflow = await workflow_service.create_workflow("demo")
    graph = Graph()
    graph.add_node(NodeInstance.create("test.sink"))  # required input unfed
    await workflow_service.update_draft(
        workflow.id, expected_revision=1, name="demo", draft=WorkflowDraft(graph=graph)
    )
    with pytest.raises(WorkflowValidationFailedError) as exc:
        await workflow_service.publish_workflow(workflow.id)
    codes = {i.code for i in exc.value.result.issues}
    assert ValidationCode.MISSING_REQUIRED_INPUT in codes
    issues = exc.value.details["issues"]
    assert isinstance(issues, list) and issues

    loaded = await workflow_service.get_workflow(workflow.id)
    assert (loaded.current_version, loaded.revision) == (0, 2)
    assert loaded.status is WorkflowStatus.DRAFT
    page = await workflow_service.list_versions(workflow.id)
    assert page.items == []


async def test_publish_creates_immutable_numbered_versions(
    workflow_service: WorkflowService,
) -> None:
    workflow_id, v1 = await published_workflow(workflow_service)
    assert v1.version == 1
    workflow = await workflow_service.get_workflow(workflow_id)
    assert workflow.status is WorkflowStatus.PUBLISHED
    assert (workflow.current_version, workflow.revision) == (1, 3)

    # Rewrite the draft completely, then publish again.
    await workflow_service.update_draft(
        workflow_id, expected_revision=3, name="demo", draft=valid_draft("v2")
    )
    v2 = await workflow_service.publish_workflow(workflow_id, expected_revision=4)
    assert v2.version == 2

    stored_v1 = await workflow_service.get_version(workflow_id, 1)
    assert stored_v1.id == v1.id
    assert stored_v1.description == "v1"
    assert set(stored_v1.graph.nodes) == set(v1.graph.nodes)
    assert set(stored_v1.graph.nodes).isdisjoint(v2.graph.nodes)

    page = await workflow_service.list_versions(workflow_id)
    assert [v.version for v in page.items] == [1, 2]


async def test_publish_with_stale_revision_is_rejected(
    workflow_service: WorkflowService,
) -> None:
    workflow_id, _ = await published_workflow(workflow_service)
    with pytest.raises(ConcurrentModificationError):
        await workflow_service.publish_workflow(workflow_id, expected_revision=1)
    assert (await workflow_service.get_workflow(workflow_id)).current_version == 1


async def test_concurrent_publishes_create_exactly_one_version(
    workflow_service: WorkflowService,
) -> None:
    workflow = await workflow_service.create_workflow("demo")
    await workflow_service.update_draft(
        workflow.id, expected_revision=1, name="demo", draft=valid_draft()
    )
    results = await asyncio.gather(
        workflow_service.publish_workflow(workflow.id),
        workflow_service.publish_workflow(workflow.id),
        return_exceptions=True,
    )
    versions = [r for r in results if isinstance(r, WorkflowVersion)]
    conflicts = [r for r in results if isinstance(r, ConcurrentModificationError)]
    assert (len(versions), len(conflicts)) == (1, 1)
    page = await workflow_service.list_versions(workflow.id)
    assert [v.version for v in page.items] == [1]


class _ExplodingVersions(SqlAlchemyWorkflowVersionRepository):
    async def save(self, workflow_id: str, version: WorkflowVersion) -> None:
        raise RuntimeError("simulated failure between the two publish writes")


class _ExplodingUow(SqlAlchemyUnitOfWork):
    def __init__(self, database: Database) -> None:
        super().__init__(database)
        self.versions = _ExplodingVersions(self._session)


async def test_publish_is_atomic(
    database: Database,
    workflow_service: WorkflowService,
    node_definitions: dict[str, NodeDefinition],
) -> None:
    """The workflow row is already updated when the version insert fails;
    the rollback must leave no half-published state behind (AGENTS §19)."""
    workflow = await workflow_service.create_workflow("demo")
    await workflow_service.update_draft(
        workflow.id, expected_revision=1, name="demo", draft=valid_draft()
    )
    exploding = WorkflowService(lambda: _ExplodingUow(database), node_definitions)
    with pytest.raises(RuntimeError, match="simulated"):
        await exploding.publish_workflow(workflow.id)

    loaded = await workflow_service.get_workflow(workflow.id)
    assert (loaded.current_version, loaded.revision) == (0, 2)
    assert loaded.status is WorkflowStatus.DRAFT
    # And the next real publish still gets version 1 — no gap, no duplicate.
    assert (await workflow_service.publish_workflow(workflow.id)).version == 1


async def test_delete_unpublished_workflow(workflow_service: WorkflowService) -> None:
    workflow = await workflow_service.create_workflow("demo")
    await workflow_service.delete_workflow(workflow.id)
    with pytest.raises(WorkflowNotFoundError):
        await workflow_service.get_workflow(workflow.id)
    with pytest.raises(WorkflowNotFoundError):
        await workflow_service.delete_workflow(workflow.id)


async def test_delete_published_workflow_is_refused(
    workflow_service: WorkflowService,
) -> None:
    workflow_id, _ = await published_workflow(workflow_service)
    with pytest.raises(WorkflowHasVersionsError):
        await workflow_service.delete_workflow(workflow_id)
    assert (await workflow_service.get_version(workflow_id, 1)).version == 1


async def test_archive_retires_a_published_workflow(
    workflow_service: WorkflowService, database: Database
) -> None:
    """Archiving is how a published workflow is retired (vision §7): the
    history stays, but nothing can run it and its triggers disappear."""
    workflow_id, _ = await published_workflow(workflow_service)
    executions = ExecutionService(lambda: SqlAlchemyUnitOfWork(database))

    async def latest_ids() -> list[str]:
        async with SqlAlchemyUnitOfWork(database) as uow:
            return [pid for pid, _ in await uow.versions.list_latest()]

    async def listed(*statuses: WorkflowStatus) -> list[str]:
        page = await workflow_service.list_workflows(statuses=statuses)
        return [w.id for w in page.items]

    archived = await workflow_service.set_archived(workflow_id, True)
    assert archived.status is WorkflowStatus.ARCHIVED
    assert await latest_ids() == []
    assert await listed() == []
    assert await listed(WorkflowStatus.ARCHIVED) == [workflow_id]
    with pytest.raises(WorkflowArchivedError):
        await executions.create_execution(workflow_id, 1, dispatch=False)
    with pytest.raises(WorkflowHasVersionsError):
        await workflow_service.delete_workflow(workflow_id)
    assert (await workflow_service.get_version(workflow_id, 1)).version == 1

    restored = await workflow_service.set_archived(workflow_id, False)
    assert restored.status is WorkflowStatus.PUBLISHED
    assert await latest_ids() == [workflow_id]
    assert await listed() == [workflow_id]
    await executions.create_execution(workflow_id, 1, dispatch=False)


async def test_unarchive_never_published_returns_to_draft(
    workflow_service: WorkflowService,
) -> None:
    workflow = await workflow_service.create_workflow("demo")
    await workflow_service.set_archived(workflow.id, True)
    restored = await workflow_service.set_archived(workflow.id, False)
    assert restored.status is WorkflowStatus.DRAFT


async def test_version_lookups_on_missing_data(
    workflow_service: WorkflowService,
) -> None:
    workflow = await workflow_service.create_workflow("demo")
    with pytest.raises(WorkflowVersionNotFoundError):
        await workflow_service.get_version(workflow.id, 1)
    with pytest.raises(WorkflowNotFoundError):
        await workflow_service.list_versions("wf_" + "0" * 32)
