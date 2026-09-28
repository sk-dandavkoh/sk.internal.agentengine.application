"""ProjectService against real PostgreSQL (roadmap Phase 06).

Exercises the use cases through the real Unit of Work: lifecycle, version
immutability, optimistic concurrency under actual row locks, and publish
atomicity. No persistence mocks (backend/AGENTS.md §54/§61).
"""

import asyncio

import pytest

from akasha.application.persistence import ConcurrentModificationError
from akasha.application.projects import (
    ProjectHasVersionsError,
    ProjectNotFoundError,
    ProjectService,
    ProjectValidationFailedError,
    ProjectVersionNotFoundError,
)
from akasha.domain.graph import Edge, Graph, NodeInstance
from akasha.domain.graph_validation import ValidationCode
from akasha.domain.node import NodeDefinition
from akasha.domain.project import ProjectDraft, ProjectStatus, ProjectVersion
from akasha.errors import DomainValidationError
from akasha.infrastructure.database import Database
from akasha.infrastructure.persistence import (
    SqlAlchemyProjectVersionRepository,
    SqlAlchemyUnitOfWork,
)

pytestmark = pytest.mark.usefixtures("clean_db")


def valid_draft(description: str = "") -> ProjectDraft:
    source = NodeInstance.create("test.source")
    sink = NodeInstance.create("test.sink", config={"prefix": ">"})
    graph = Graph()
    graph.add_node(source)
    graph.add_node(sink)
    graph.add_edge(Edge(source.id, "text", sink.id, "text"))
    return ProjectDraft(graph=graph, description=description)


async def published_project(svc: ProjectService) -> tuple[str, ProjectVersion]:
    project = await svc.create_project("demo")
    await svc.update_draft(
        project.id, expected_revision=1, name="demo", draft=valid_draft("v1")
    )
    return project.id, await svc.publish_project(project.id)


async def test_create_and_get_round_trip(project_service: ProjectService) -> None:
    created = await project_service.create_project("demo", description="about")
    loaded = await project_service.get_project(created.id)
    assert (loaded.name, loaded.draft.description) == ("demo", "about")
    assert (loaded.revision, loaded.current_version) == (1, 0)
    assert loaded.status is ProjectStatus.DRAFT


async def test_create_rejects_blank_name(project_service: ProjectService) -> None:
    with pytest.raises(DomainValidationError):
        await project_service.create_project("  ")


async def test_get_unknown_project(project_service: ProjectService) -> None:
    with pytest.raises(ProjectNotFoundError):
        await project_service.get_project("proj_" + "0" * 32)


async def test_list_is_paginated(project_service: ProjectService) -> None:
    ids = [(await project_service.create_project(f"p{i}")).id for i in range(3)]
    first = await project_service.list_projects(offset=0, limit=2)
    rest = await project_service.list_projects(offset=2, limit=2)
    assert [p.id for p in first.items] == ids[:2] and first.has_more
    assert [p.id for p in rest.items] == ids[2:] and not rest.has_more


@pytest.mark.parametrize(("offset", "limit"), [(-1, 10), (0, 0), (0, 101)])
async def test_list_rejects_bad_page(
    project_service: ProjectService, offset: int, limit: int
) -> None:
    with pytest.raises(DomainValidationError):
        await project_service.list_projects(offset=offset, limit=limit)


async def test_update_draft_persists_and_advances_revision(
    project_service: ProjectService,
) -> None:
    project = await project_service.create_project("demo")
    draft = valid_draft("edited")
    updated = await project_service.update_draft(
        project.id, expected_revision=1, name="renamed", draft=draft
    )
    assert updated.revision == 2
    loaded = await project_service.get_project(project.id)
    assert (loaded.name, loaded.revision) == ("renamed", 2)
    assert set(loaded.draft.graph.nodes) == set(draft.graph.nodes)
    assert loaded.draft.description == "edited"


async def test_update_draft_accepts_semantically_invalid_draft(
    project_service: ProjectService,
) -> None:
    """A draft is work in progress; only publish demands validity."""
    project = await project_service.create_project("demo")
    graph = Graph()
    graph.add_node(NodeInstance.create("no.such_node"))
    await project_service.update_draft(
        project.id, expected_revision=1, name="demo", draft=ProjectDraft(graph=graph)
    )
    result = await project_service.validate_project(project.id)
    codes = {i.code for i in result.issues}
    assert ValidationCode.UNKNOWN_NODE_TYPE in codes


async def test_stale_revision_is_rejected_without_writing(
    project_service: ProjectService,
) -> None:
    project = await project_service.create_project("demo")
    await project_service.update_draft(
        project.id, expected_revision=1, name="v2", draft=ProjectDraft()
    )
    with pytest.raises(ConcurrentModificationError) as exc:
        await project_service.update_draft(
            project.id, expected_revision=1, name="stale", draft=ProjectDraft()
        )
    assert exc.value.details["current_revision"] == 2
    assert (await project_service.get_project(project.id)).name == "v2"


async def test_concurrent_updates_with_same_revision_one_wins(
    project_service: ProjectService,
) -> None:
    """Whatever the interleaving (in-memory revision check or database CAS
    after a row-lock wait — the latter is pinned deterministically in
    test_persistence), exactly one writer commits."""
    project = await project_service.create_project("demo")
    results = await asyncio.gather(
        *(
            project_service.update_draft(
                project.id,
                expected_revision=1,
                name=f"writer-{i}",
                draft=ProjectDraft(),
            )
            for i in range(2)
        ),
        return_exceptions=True,
    )
    conflicts = [r for r in results if isinstance(r, ConcurrentModificationError)]
    assert len(conflicts) == 1
    loaded = await project_service.get_project(project.id)
    assert loaded.revision == 2
    assert loaded.name in {"writer-0", "writer-1"}


async def test_validate_valid_and_invalid_drafts(
    project_service: ProjectService,
) -> None:
    project = await project_service.create_project("demo")
    empty = await project_service.validate_project(project.id)
    assert [i.code for i in empty.issues] == [ValidationCode.EMPTY_GRAPH]

    await project_service.update_draft(
        project.id, expected_revision=1, name="demo", draft=valid_draft()
    )
    assert (await project_service.validate_project(project.id)).ok


async def test_publish_invalid_draft_persists_nothing(
    project_service: ProjectService,
) -> None:
    project = await project_service.create_project("demo")
    graph = Graph()
    graph.add_node(NodeInstance.create("test.sink"))  # required input unfed
    await project_service.update_draft(
        project.id, expected_revision=1, name="demo", draft=ProjectDraft(graph=graph)
    )
    with pytest.raises(ProjectValidationFailedError) as exc:
        await project_service.publish_project(project.id)
    codes = {i.code for i in exc.value.result.issues}
    assert ValidationCode.MISSING_REQUIRED_INPUT in codes
    issues = exc.value.details["issues"]
    assert isinstance(issues, list) and issues

    loaded = await project_service.get_project(project.id)
    assert (loaded.current_version, loaded.revision) == (0, 2)
    assert loaded.status is ProjectStatus.DRAFT
    page = await project_service.list_versions(project.id)
    assert page.items == []


async def test_publish_creates_immutable_numbered_versions(
    project_service: ProjectService,
) -> None:
    project_id, v1 = await published_project(project_service)
    assert v1.version == 1
    project = await project_service.get_project(project_id)
    assert project.status is ProjectStatus.PUBLISHED
    assert (project.current_version, project.revision) == (1, 3)

    # Rewrite the draft completely, then publish again.
    await project_service.update_draft(
        project_id, expected_revision=3, name="demo", draft=valid_draft("v2")
    )
    v2 = await project_service.publish_project(project_id, expected_revision=4)
    assert v2.version == 2

    stored_v1 = await project_service.get_version(project_id, 1)
    assert stored_v1.id == v1.id
    assert stored_v1.description == "v1"
    assert set(stored_v1.graph.nodes) == set(v1.graph.nodes)
    assert set(stored_v1.graph.nodes).isdisjoint(v2.graph.nodes)

    page = await project_service.list_versions(project_id)
    assert [v.version for v in page.items] == [1, 2]


async def test_publish_with_stale_revision_is_rejected(
    project_service: ProjectService,
) -> None:
    project_id, _ = await published_project(project_service)
    with pytest.raises(ConcurrentModificationError):
        await project_service.publish_project(project_id, expected_revision=1)
    assert (await project_service.get_project(project_id)).current_version == 1


async def test_concurrent_publishes_create_exactly_one_version(
    project_service: ProjectService,
) -> None:
    project = await project_service.create_project("demo")
    await project_service.update_draft(
        project.id, expected_revision=1, name="demo", draft=valid_draft()
    )
    results = await asyncio.gather(
        project_service.publish_project(project.id),
        project_service.publish_project(project.id),
        return_exceptions=True,
    )
    versions = [r for r in results if isinstance(r, ProjectVersion)]
    conflicts = [r for r in results if isinstance(r, ConcurrentModificationError)]
    assert (len(versions), len(conflicts)) == (1, 1)
    page = await project_service.list_versions(project.id)
    assert [v.version for v in page.items] == [1]


class _ExplodingVersions(SqlAlchemyProjectVersionRepository):
    async def save(self, project_id: str, version: ProjectVersion) -> None:
        raise RuntimeError("simulated failure between the two publish writes")


class _ExplodingUow(SqlAlchemyUnitOfWork):
    def __init__(self, database: Database) -> None:
        super().__init__(database)
        self.versions = _ExplodingVersions(self._session)


async def test_publish_is_atomic(
    database: Database,
    project_service: ProjectService,
    node_definitions: dict[str, NodeDefinition],
) -> None:
    """The project row is already updated when the version insert fails;
    the rollback must leave no half-published state behind (AGENTS §19)."""
    project = await project_service.create_project("demo")
    await project_service.update_draft(
        project.id, expected_revision=1, name="demo", draft=valid_draft()
    )
    exploding = ProjectService(lambda: _ExplodingUow(database), node_definitions)
    with pytest.raises(RuntimeError, match="simulated"):
        await exploding.publish_project(project.id)

    loaded = await project_service.get_project(project.id)
    assert (loaded.current_version, loaded.revision) == (0, 2)
    assert loaded.status is ProjectStatus.DRAFT
    # And the next real publish still gets version 1 — no gap, no duplicate.
    assert (await project_service.publish_project(project.id)).version == 1


async def test_delete_unpublished_project(project_service: ProjectService) -> None:
    project = await project_service.create_project("demo")
    await project_service.delete_project(project.id)
    with pytest.raises(ProjectNotFoundError):
        await project_service.get_project(project.id)
    with pytest.raises(ProjectNotFoundError):
        await project_service.delete_project(project.id)


async def test_delete_published_project_is_refused(
    project_service: ProjectService,
) -> None:
    project_id, _ = await published_project(project_service)
    with pytest.raises(ProjectHasVersionsError):
        await project_service.delete_project(project_id)
    assert (await project_service.get_version(project_id, 1)).version == 1


async def test_version_lookups_on_missing_data(project_service: ProjectService) -> None:
    project = await project_service.create_project("demo")
    with pytest.raises(ProjectVersionNotFoundError):
        await project_service.get_version(project.id, 1)
    with pytest.raises(ProjectNotFoundError):
        await project_service.list_versions("proj_" + "0" * 32)
