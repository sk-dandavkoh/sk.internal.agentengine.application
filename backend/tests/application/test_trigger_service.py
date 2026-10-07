"""TriggerService / TriggerRegistry unit tests over an in-memory UoW.

Service semantics (enabled checks, type matching, metadata, latest-version
resolution) are deterministic application logic — no database needed here;
the SQL behind ``list_latest`` is covered by the PostgreSQL integration
tests (backend/AGENTS.md §52/§54).
"""

from collections.abc import Collection, Mapping, Sequence
from datetime import datetime, timedelta
from types import TracebackType
from typing import Any, cast

import pytest

from akasha.application.executions import ExecutionService
from akasha.application.persistence import (
    AccessTokenRepository,
    CredentialRepository,
    EventRepository,
    ExecutionRepository,
    ExecutionSample,
    ExecutionSummary,
    NodeLogRepository,
    NodeSample,
    SessionRepository,
    UnitOfWork,
    UserRepository,
    WorkflowMemberRepository,
    WorkflowRepository,
    WorkflowVersionRepository,
)
from akasha.application.triggers import (
    ResolvedTrigger,
    TriggerDisabledError,
    TriggerRegistry,
    TriggerService,
)
from akasha.application.workflows import WorkflowVersionNotFoundError
from akasha.domain.event import Event
from akasha.domain.execution import Execution, ExecutionStatus, LogLevel, NodeLogEntry
from akasha.domain.graph import Graph
from akasha.domain.primitives import new_id
from akasha.domain.trigger import Trigger, TriggerContext, TriggerType
from akasha.domain.workflow import Workflow, WorkflowDraft, WorkflowVersion
from akasha.errors import DomainValidationError


class FakeVersions(WorkflowVersionRepository):
    def __init__(self) -> None:
        self.rows: dict[tuple[str, int], WorkflowVersion] = {}

    async def get(self, workflow_id: str, version: int) -> WorkflowVersion | None:
        return self.rows.get((workflow_id, version))

    async def save(self, workflow_id: str, version: WorkflowVersion) -> None:
        self.rows[(workflow_id, version.version)] = version

    async def list(
        self, workflow_id: str, *, offset: int = 0, limit: int = 100
    ) -> Sequence[WorkflowVersion]:
        return [v for (pid, _), v in sorted(self.rows.items()) if pid == workflow_id][
            offset : offset + limit
        ]

    async def list_latest(
        self, *, workflow_id: str | None = None
    ) -> Sequence[tuple[str, WorkflowVersion]]:
        latest: dict[str, WorkflowVersion] = {}
        for (pid, _), version in self.rows.items():
            if workflow_id is not None and pid != workflow_id:
                continue
            if pid not in latest or version.version > latest[pid].version:
                latest[pid] = version
        return sorted(latest.items())


class FakeExecutions(ExecutionRepository):
    def __init__(self) -> None:
        self.items: dict[str, Execution] = {}

    async def get(self, execution_id: str) -> Execution | None:
        return self.items.get(execution_id)

    async def save(self, execution: Execution) -> None:
        self.items[execution.id] = execution

    async def list_summaries(
        self,
        *,
        workflow_id: str | None = None,
        workflow_version: int | None = None,
        visible_to: str | None = None,
        statuses: Collection[ExecutionStatus] = (),
        offset: int = 0,
        limit: int = 100,
    ) -> Sequence[ExecutionSummary]:
        return []

    async def count_by_status(
        self, *, visible_to: str | None = None
    ) -> Mapping[ExecutionStatus, int]:
        return {}

    async def sample_executions(self, **_: object) -> Sequence[ExecutionSample]:
        return []

    async def sample_node_executions(self, **_: object) -> Sequence[NodeSample]:
        return []

    async def list_queued(
        self, *, created_before: datetime, limit: int
    ) -> Sequence[str]:
        return []

    async def count_unfinished(self, workflow_id: str) -> int:
        return sum(
            1
            for e in self.items.values()
            if e.workflow_id == workflow_id and not e.is_terminal
        )

    async def heartbeat(self, execution_id: str, *, revision: int) -> bool:
        return True

    async def list_stale(self, *, stale_after: timedelta, limit: int) -> Sequence[str]:
        return []

    async def claim_stale(self, execution_id: str, *, stale_after: timedelta) -> bool:
        return False

    async def list_due_waits(self, *, now: datetime, limit: int) -> Sequence[str]:
        return []

    async def claim_due_wait(self, execution_id: str, *, now: datetime) -> bool:
        return False


class FakeWorkflows(WorkflowRepository):
    """Unused by the trigger services; the UoW protocol demands the member."""

    async def get(self, workflow_id: str) -> Workflow | None:
        return None

    async def save(self, workflow: Workflow) -> None:
        raise NotImplementedError

    async def delete(self, workflow_id: str, *, revision: int) -> bool:
        return False

    async def list(self, **_: object) -> Sequence[Workflow]:
        return []

    async def using_plugin(
        self, plugin_id: str, node_types: Sequence[str]
    ) -> Sequence[str]:
        return []

    async def lock_owner(self, workflow_id: str) -> str | None:
        return None

    async def set_owner(self, workflow_id: str, owner_id: str) -> None:
        raise NotImplementedError


class FakeEvents(EventRepository):
    def __init__(self) -> None:
        self.items: list[Event] = []

    async def append(self, events: Sequence[Event]) -> None:
        self.items.extend(events)

    async def list(
        self,
        *,
        execution_id: str | None = None,
        workflow_id: str | None = None,
        after: int = 0,
        limit: int = 100,
        positions: Collection[int] | None = None,
    ) -> Sequence[Event]:
        return self.items[:limit]

    async def max_position(self) -> int:
        return len(self.items)


class FakeNodeLogs(NodeLogRepository):
    """Execution saves stage log lines; trigger paths record none."""

    async def append(self, entries: Sequence[NodeLogEntry]) -> None:
        assert not entries

    async def list(
        self,
        execution_id: str,
        *,
        after: int = 0,
        limit: int = 100,
        node_id: str | None = None,
        levels: Collection[LogLevel] = (),
        contains: str | None = None,
    ) -> Sequence[NodeLogEntry]:
        return []


class FakeUow(UnitOfWork):
    def __init__(self) -> None:
        self.versions = FakeVersions()
        self.executions = FakeExecutions()
        self.workflows = FakeWorkflows()
        self.events = FakeEvents()
        self.node_logs = FakeNodeLogs()
        # Triggers never touch credentials or accounts; any access would
        # fail loudly.
        self.credentials = cast(CredentialRepository, None)
        self.users = cast(UserRepository, None)
        self.sessions = cast(SessionRepository, None)
        self.access_tokens = cast(AccessTokenRepository, None)
        self.members = cast(WorkflowMemberRepository, None)

    async def __aenter__(self) -> "FakeUow":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        pass

    async def commit(self) -> None:
        pass


class Harness:
    def __init__(self) -> None:
        self.uow = FakeUow()
        executions = ExecutionService(lambda: self.uow)
        self.service = TriggerService(executions)
        self.registry = TriggerRegistry(lambda: self.uow)

    def publish(
        self, workflow_id: str, version: int, triggers: list[Trigger] | None = None
    ) -> WorkflowVersion:
        snapshot = WorkflowDraft(graph=Graph(), triggers=list(triggers or [])).publish(
            version=version
        )
        self.uow.versions.rows[(workflow_id, version)] = snapshot
        return snapshot


def api_trigger(**kwargs: Any) -> Trigger:
    return Trigger.create(TriggerType.API, **kwargs)


class TestFire:
    async def test_fire_creates_queued_execution_with_metadata(self) -> None:
        harness = Harness()
        trigger = api_trigger()
        workflow_id = new_id("wf")
        harness.publish(workflow_id, 1, [trigger])

        execution = await harness.service.fire(
            ResolvedTrigger(workflow_id, 1, trigger),
            TriggerContext(
                trigger_type=TriggerType.API,
                payload={"name": "ada"},
                source="api",
                metadata={"correlation_id": "req-1"},
            ),
        )

        assert execution.status is ExecutionStatus.QUEUED
        assert execution.input == {"name": "ada"}
        assert execution.metadata.trigger_type is TriggerType.API
        assert execution.metadata.trigger_id == trigger.id
        assert execution.metadata.correlation_id == "req-1"
        # Convergence: persisted via ExecutionService (§32) — the same
        # canonical Execution every trigger type produces.
        stored = await harness.uow.executions.get(execution.id)
        assert stored is execution

    async def test_fire_disabled_trigger_rejected(self) -> None:
        harness = Harness()
        trigger = api_trigger()
        trigger.enabled = False
        workflow_id = new_id("wf")
        harness.publish(workflow_id, 1, [trigger])

        with pytest.raises(TriggerDisabledError):
            await harness.service.fire(
                ResolvedTrigger(workflow_id, 1, trigger),
                TriggerContext(trigger_type=TriggerType.API),
            )

    async def test_fire_context_type_mismatch_rejected(self) -> None:
        harness = Harness()
        trigger = api_trigger()
        workflow_id = new_id("wf")
        harness.publish(workflow_id, 1, [trigger])

        with pytest.raises(DomainValidationError, match="does not match"):
            await harness.service.fire(
                ResolvedTrigger(workflow_id, 1, trigger),
                TriggerContext(trigger_type=TriggerType.WEBHOOK),
            )

    async def test_fire_unknown_version_rejected(self) -> None:
        harness = Harness()
        with pytest.raises(WorkflowVersionNotFoundError):
            await harness.service.fire(
                ResolvedTrigger(new_id("wf"), 42, api_trigger()),
                TriggerContext(trigger_type=TriggerType.API),
            )

    async def test_fire_manual(self) -> None:
        harness = Harness()
        workflow_id = new_id("wf")
        harness.publish(workflow_id, 3)

        execution = await harness.service.fire_manual(
            workflow_id, 3, input={"q": "x"}, correlation_id="req-9"
        )

        assert execution.status is ExecutionStatus.QUEUED
        assert execution.metadata.trigger_type is TriggerType.MANUAL
        assert execution.metadata.trigger_id is None
        assert execution.metadata.correlation_id == "req-9"
        assert execution.input == {"q": "x"}


class TestRegistry:
    async def test_resolve_finds_trigger_on_latest_version(self) -> None:
        harness = Harness()
        trigger = api_trigger()
        workflow_id = new_id("wf")
        harness.publish(workflow_id, 1, [trigger])
        # Trigger ids are stable across publishes (draft copies keep ids).
        harness.publish(workflow_id, 2, [api_trigger(), trigger])

        resolved = await harness.registry.resolve(workflow_id, trigger.id)

        assert resolved is not None
        assert (resolved.workflow_id, resolved.version) == (workflow_id, 2)
        assert resolved.trigger.id == trigger.id

    async def test_resolve_ignores_triggers_retired_by_newer_version(self) -> None:
        harness = Harness()
        trigger = api_trigger()
        workflow_id = new_id("wf")
        harness.publish(workflow_id, 1, [trigger])
        harness.publish(workflow_id, 2)  # republished without the trigger

        assert await harness.registry.resolve(workflow_id, trigger.id) is None

    async def test_resolve_never_sees_drafts(self) -> None:
        harness = Harness()
        assert await harness.registry.resolve(new_id("wf"), new_id("trig")) is None

    async def test_resolve_filters_by_type(self) -> None:
        harness = Harness()
        trigger = api_trigger()
        workflow_id = new_id("wf")
        harness.publish(workflow_id, 1, [trigger])

        assert (
            await harness.registry.resolve(
                workflow_id, trigger.id, trigger_type=TriggerType.API
            )
            is not None
        )
        assert (
            await harness.registry.resolve(
                workflow_id, trigger.id, trigger_type=TriggerType.WEBHOOK
            )
            is None
        )

    async def test_resolution_is_scoped_by_workflow(self) -> None:
        # The same trigger id published by two workflows never crosses over:
        # each workflow's URL reaches only its own trigger (D6).
        harness = Harness()
        tid = new_id("trig")
        workflow_a, workflow_b = new_id("wf"), new_id("wf")
        harness.publish(workflow_a, 1, [Trigger(id=tid, trigger_type=TriggerType.API)])
        harness.publish(workflow_b, 1, [Trigger(id=tid, trigger_type=TriggerType.API)])

        for workflow_id in (workflow_a, workflow_b):
            resolved = await harness.registry.resolve(workflow_id, tid)
            assert resolved is not None
            assert resolved.workflow_id == workflow_id
        assert await harness.registry.resolve(new_id("wf"), tid) is None

    async def test_list_scheduled_only_enabled_cron_interval_latest(self) -> None:
        harness = Harness()
        cron = Trigger.create(TriggerType.CRON, {"cron": "0 * * * *"})
        disabled = Trigger.create(TriggerType.INTERVAL, {"interval_seconds": 5})
        disabled.enabled = False
        manual = Trigger.create(TriggerType.MANUAL)
        retired = Trigger.create(TriggerType.INTERVAL, {"interval_seconds": 5})
        other = Trigger.create(TriggerType.INTERVAL, {"interval_seconds": 30})

        workflow_a, workflow_b = new_id("wf"), new_id("wf")
        harness.publish(workflow_a, 1, [retired])
        harness.publish(workflow_a, 2, [cron, disabled, manual])
        harness.publish(workflow_b, 1, [other])

        scheduled = await harness.registry.list_scheduled()

        ids = [r.trigger.id for r in scheduled]
        assert ids == sorted([cron.id, other.id])

        by_id = {r.trigger.id: r for r in scheduled}
        assert (by_id[cron.id].workflow_id, by_id[cron.id].version) == (workflow_a, 2)
        assert (by_id[other.id].workflow_id, by_id[other.id].version) == (workflow_b, 1)

    async def test_list_scheduled_keeps_same_id_in_two_workflows(self) -> None:
        harness = Harness()
        tid = new_id("trig")
        config = {"interval_seconds": 30}
        workflow_a, workflow_b = sorted([new_id("wf"), new_id("wf")])
        for workflow_id in (workflow_b, workflow_a):
            harness.publish(
                workflow_id,
                1,
                [Trigger(id=tid, trigger_type=TriggerType.INTERVAL, config=config)],
            )

        scheduled = await harness.registry.list_scheduled()

        assert [r.workflow_id for r in scheduled] == [workflow_a, workflow_b]
