"""Draft/publish lifecycle invariants (architecture §3.7)."""

from dataclasses import FrozenInstanceError, asdict
from datetime import UTC, datetime

import pytest

from akasha.domain.graph import Graph, NodeInstance
from akasha.domain.graph_validation import ValidationCode
from akasha.domain.node import NodeDefinition, NodeMetadata
from akasha.domain.primitives import new_id
from akasha.domain.trigger import Trigger, TriggerType
from akasha.domain.workflow import (
    Dependency,
    Workflow,
    WorkflowDraft,
    WorkflowStatus,
    WorkflowVersion,
    validate_dependencies,
    validate_triggers,
)
from akasha.errors import DomainValidationError


def _workflow_with_node() -> tuple[Workflow, NodeInstance]:
    workflow = Workflow.create(name="Lead Qualification")
    node = NodeInstance.create(node_type="core.llm", config={"model": "x"})
    workflow.draft.graph.add_node(node)
    return workflow, node


class TestWorkflow:
    def test_create_defaults(self) -> None:
        p = Workflow.create(name="Demo")
        assert p.status is WorkflowStatus.DRAFT
        assert p.current_version == 0
        assert p.draft.graph.nodes == {}
        assert p.timestamps.created_at.tzinfo is not None

    def test_blank_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="blank"):
            Workflow.create(name="   ")


class TestPublish:
    def test_publish_creates_version_1_then_increments(self) -> None:
        p = Workflow.create(name="Demo")
        v1 = p.publish_draft()
        v2 = p.publish_draft()
        assert (v1.version, v2.version) == (1, 2)
        assert p.current_version == 2
        assert p.status is WorkflowStatus.PUBLISHED

    def test_replace_draft_keeps_versions_and_touches_timestamp(self) -> None:
        p = Workflow.create(name="Demo", description="old")
        published = p.publish_draft()
        before = p.timestamps
        new_draft = WorkflowDraft(description="new")
        p.replace_draft(new_draft, name="Renamed")
        assert (p.name, p.draft) == ("Renamed", new_draft)
        assert p.current_version == 1
        assert p.status is WorkflowStatus.PUBLISHED
        assert published.description == "old"
        assert p.timestamps.created_at == before.created_at
        assert p.timestamps.updated_at >= before.updated_at

    def test_replace_draft_rejects_blank_name(self) -> None:
        p = Workflow.create(name="Demo")
        with pytest.raises(DomainValidationError, match="blank"):
            p.replace_draft(WorkflowDraft(), name=" ")
        assert p.name == "Demo"

    def test_negative_counters_rejected(self) -> None:
        with pytest.raises(DomainValidationError, match="counters"):
            Workflow(id=new_id("wf"), name="x", current_version=-1)

    def test_published_version_is_frozen(self) -> None:
        v = Workflow.create(name="Demo").publish_draft()
        with pytest.raises(FrozenInstanceError):
            v.version = 99  # type: ignore[misc]

    def test_draft_mutation_after_publish_does_not_change_version(self) -> None:
        """Snapshot isolation: the core invariant of the whole phase."""
        workflow, node = _workflow_with_node()
        workflow.draft.triggers.append(
            Trigger.create(TriggerType.INTERVAL, {"interval_seconds": 60})
        )
        workflow.draft.description = "before"
        version = workflow.publish_draft()
        snapshot = asdict(version)

        # mutate the draft every way possible
        node.config["model"] = "changed"
        node.label = "changed"
        workflow.draft.graph.remove_node(node.id)
        workflow.draft.triggers.clear()
        workflow.draft.description = "after"

        assert asdict(version) == snapshot
        assert node.id in version.graph.nodes
        assert version.graph.nodes[node.id].config == {"model": "x"}
        assert len(version.triggers) == 1

    def test_version_number_below_one_rejected(self) -> None:
        with pytest.raises(DomainValidationError, match=">= 1"):
            WorkflowDraft().publish(version=0)

    def test_publish_with_explicit_timestamp(self) -> None:
        when = datetime(2026, 1, 1, tzinfo=UTC)
        v = Workflow.create(name="Demo").publish_draft(published_at=when)
        assert v.published_at == when

    def test_version_requires_valid_ids_and_versions(self) -> None:
        with pytest.raises(ValueError, match="malformed"):
            WorkflowVersion(id="bad", version=1, schema_version=1, graph=Graph())
        with pytest.raises(DomainValidationError, match="schema_version"):
            WorkflowVersion(
                id=new_id("wfver"), version=1, schema_version=0, graph=Graph()
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


def _definition(node_id: str, version: str) -> NodeDefinition:
    return NodeDefinition(
        metadata=NodeMetadata(
            node_id=node_id, name=node_id, description="D", version=version
        )
    )


_DEFS = {"core.http": _definition("core.http", "1.4.2")}


class TestDependencyValidation:
    """Node version compatibility (architecture §3.8/§144): declared node
    dependencies are checked at validate/publish and before every run."""

    def codes(self, *deps: Dependency) -> list[ValidationCode]:
        return [i.code for i in validate_dependencies(deps, _DEFS)]

    def test_satisfied_constraints_pass(self) -> None:
        for constraint in ("*", "1.4.2", "==1.4.2", ">=1.0.0,<2.0.0", ">1.4.1"):
            assert self.codes(Dependency("node", "core.http", constraint)) == []

    def test_unsatisfied_constraint_is_incompatible(self) -> None:
        for constraint in (">=2.0.0", "<1.4.2", "1.4.3", ">=1.0.0,<1.4.0"):
            assert self.codes(Dependency("node", "core.http", constraint)) == [
                ValidationCode.INCOMPATIBLE_DEPENDENCY
            ]

    def test_missing_node_type(self) -> None:
        assert self.codes(Dependency("node", "core.nope")) == [
            ValidationCode.MISSING_DEPENDENCY
        ]

    def test_malformed_constraint_is_an_issue_not_an_exception(self) -> None:
        for constraint in ("^1.0.0", "latest", ">=1.0", ">=1.0.0,,"):
            assert self.codes(Dependency("node", "core.http", constraint)) == [
                ValidationCode.INVALID_DEPENDENCY
            ]

    def test_plugin_dependencies_are_left_to_the_plugin_system(self) -> None:
        assert self.codes(Dependency("plugin", "community.web", ">=9.0.0")) == []

    def test_draft_and_version_validation_include_dependencies(self) -> None:
        draft = WorkflowDraft(dependencies=[Dependency("node", "core.nope")])
        for result in (
            draft.validate(_DEFS),
            draft.publish(version=1).validate(_DEFS),
        ):
            assert result.by_code(ValidationCode.MISSING_DEPENDENCY)
            assert result.by_code(ValidationCode.EMPTY_GRAPH)  # graph still checked


class TestTriggerValidation:
    """Trigger ids are unique per workflow and schedules must be evaluable.
    Both rules gate publishing as issues, never as construction errors, so
    persisted drafts stay loadable and fixable (architecture §125)."""

    def test_duplicate_trigger_ids_are_an_issue(self) -> None:
        tid = new_id("trig")
        draft = WorkflowDraft(
            triggers=[
                Trigger(
                    id=tid,
                    trigger_type=TriggerType.WEBHOOK,
                    config={"secret_credential": "credential:hook"},
                ),
                Trigger(id=tid, trigger_type=TriggerType.API),
            ]
        )
        issues = validate_triggers(draft.triggers)
        assert [(i.code, i.trigger_id) for i in issues] == [
            (ValidationCode.DUPLICATE_TRIGGER_ID, tid)
        ]
        assert draft.validate(_DEFS).by_code(ValidationCode.DUPLICATE_TRIGGER_ID)

    def test_webhooks_must_name_their_signing_credential(self) -> None:
        unsigned = Trigger.create(TriggerType.WEBHOOK)
        not_a_ref = Trigger.create(TriggerType.WEBHOOK, {"secret_credential": 42})
        signed = Trigger.create(
            TriggerType.WEBHOOK, {"secret_credential": "credential:hook"}
        )
        issues = validate_triggers([unsigned, signed, not_a_ref])
        assert [(i.code, i.trigger_id) for i in issues] == [
            (ValidationCode.WEBHOOK_SECRET_REQUIRED, unsigned.id),
            (ValidationCode.WEBHOOK_SECRET_REQUIRED, not_a_ref.id),
        ]

    def test_unschedulable_triggers_are_issues_with_location(self) -> None:
        never = Trigger.create(TriggerType.CRON, {"cron": "0 0 31 2 *"})
        too_long = Trigger.create(TriggerType.INTERVAL, {"interval_seconds": 1e12})
        fine = Trigger.create(TriggerType.CRON, {"cron": "*/5 * * * *"})
        issues = validate_triggers([never, fine, too_long])
        assert [(i.code, i.trigger_id) for i in issues] == [
            (ValidationCode.INVALID_TRIGGER_SCHEDULE, never.id),
            (ValidationCode.INVALID_TRIGGER_SCHEDULE, too_long.id),
        ]

    def test_trigger_rules_do_not_block_running_a_published_version(self) -> None:
        # WorkflowVersion.validate is the runtime's pre-run check: how a run
        # was triggered must not stop a published version from executing.
        draft = WorkflowDraft(
            triggers=[Trigger.create(TriggerType.CRON, {"cron": "0 0 31 2 *"})]
        )
        assert draft.validate(_DEFS).by_code(ValidationCode.INVALID_TRIGGER_SCHEDULE)
        version_result = draft.publish(version=1).validate(_DEFS)
        assert not version_result.by_code(ValidationCode.INVALID_TRIGGER_SCHEDULE)
