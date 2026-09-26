"""Draft/publish lifecycle invariants (architecture §3.7)."""

from dataclasses import FrozenInstanceError, asdict
from datetime import UTC, datetime

import pytest

from akasha.domain.graph import Graph, NodeInstance
from akasha.domain.primitives import new_id
from akasha.domain.project import (
    Dependency,
    Project,
    ProjectDraft,
    ProjectStatus,
    ProjectVersion,
)
from akasha.domain.trigger import Trigger, TriggerType
from akasha.errors import DomainValidationError


def _project_with_node() -> tuple[Project, NodeInstance]:
    project = Project.create(name="Lead Qualification")
    node = NodeInstance.create(node_type="core.llm", config={"model": "x"})
    project.draft.graph.add_node(node)
    return project, node


class TestProject:
    def test_create_defaults(self) -> None:
        p = Project.create(name="Demo")
        assert p.status is ProjectStatus.DRAFT
        assert p.current_version == 0
        assert p.draft.graph.nodes == {}
        assert p.timestamps.created_at.tzinfo is not None

    def test_blank_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="blank"):
            Project.create(name="   ")


class TestPublish:
    def test_publish_creates_version_1_then_increments(self) -> None:
        p = Project.create(name="Demo")
        v1 = p.publish_draft()
        v2 = p.publish_draft()
        assert (v1.version, v2.version) == (1, 2)
        assert p.versions == [v1, v2]
        assert p.status is ProjectStatus.PUBLISHED

    def test_published_version_is_frozen(self) -> None:
        v = Project.create(name="Demo").publish_draft()
        with pytest.raises(FrozenInstanceError):
            v.version = 99  # type: ignore[misc]

    def test_draft_mutation_after_publish_does_not_change_version(self) -> None:
        """Snapshot isolation: the core invariant of the whole phase."""
        project, node = _project_with_node()
        project.draft.triggers.append(
            Trigger.create(TriggerType.INTERVAL, {"interval_seconds": 60})
        )
        project.draft.description = "before"
        version = project.publish_draft()
        snapshot = asdict(version)

        # mutate the draft every way possible
        node.config["model"] = "changed"
        node.label = "changed"
        project.draft.graph.remove_node(node.id)
        project.draft.triggers.clear()
        project.draft.description = "after"

        assert asdict(version) == snapshot
        assert node.id in version.graph.nodes
        assert version.graph.nodes[node.id].config == {"model": "x"}
        assert len(version.triggers) == 1

    def test_version_number_below_one_rejected(self) -> None:
        with pytest.raises(DomainValidationError, match=">= 1"):
            ProjectDraft().publish(version=0)

    def test_publish_with_explicit_timestamp(self) -> None:
        when = datetime(2026, 1, 1, tzinfo=UTC)
        v = Project.create(name="Demo").publish_draft(published_at=when)
        assert v.published_at == when

    def test_version_requires_valid_ids_and_versions(self) -> None:
        with pytest.raises(ValueError, match="malformed"):
            ProjectVersion(id="bad", version=1, schema_version=1, graph=Graph())
        with pytest.raises(DomainValidationError, match="schema_version"):
            ProjectVersion(
                id=new_id("pver"), version=1, schema_version=0, graph=Graph()
            )


class TestDependency:
    def test_valid(self) -> None:
        d = Dependency(
            kind="plugin", name="community.web", version_constraint=">=1.0.0"
        )
        assert d.kind == "plugin"

    def test_unknown_kind_rejected(self) -> None:
        with pytest.raises(DomainValidationError, match="dependency kind"):
            Dependency(kind="service", name="x")

    def test_blank_constraint_rejected(self) -> None:
        with pytest.raises(DomainValidationError, match="constraint"):
            Dependency(kind="node", name="core.llm", version_constraint=" ")
