"""Trigger System (roadmap Phase 10, architecture §17, backend/AGENTS §32).

Every trigger mechanism (manual run, API invoke, webhook URL, scheduler tick)
normalizes into a ``TriggerContext`` and converges here; firing means exactly
one thing: ``ExecutionService.create_execution`` on the resolved immutable
ProjectVersion. No trigger type implements its own execution logic.

Resolution rule (architecture §125): a configured trigger lives on the
LATEST published version of its project. Publishing retires the previous
version's triggers. Drafts never fire, so a trigger can never ambiguously
execute "whatever happens to be the current draft". Trigger ids are unique
only within a project (DUPLICATE_TRIGGER_ID blocks publish), so resolution
is always scoped by project id.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from akasha.application.executions import ExecutionService
from akasha.application.projects import UowFactory
from akasha.domain.execution import Execution, ExecutionMetadata
from akasha.domain.primitives import new_id
from akasha.domain.trigger import Trigger, TriggerContext, TriggerType
from akasha.errors import AkashaError, DomainValidationError

logger = logging.getLogger("akasha.application.triggers")

#: Trigger types callable through the (programmatic) invoke endpoint.
INVOCABLE_TYPES = frozenset({TriggerType.API, TriggerType.MANUAL})

SCHEDULED_TYPES = frozenset({TriggerType.CRON, TriggerType.INTERVAL})


class TriggerNotFoundError(AkashaError):
    code = "TRIGGER_NOT_FOUND"
    http_status = 404


class TriggerDisabledError(AkashaError):
    code = "TRIGGER_DISABLED"
    http_status = 409


@dataclass(frozen=True)
class ResolvedTrigger:
    """A configured trigger pinned to its immutable project version."""

    project_id: str
    version: int
    trigger: Trigger


class TriggerRegistry:
    """Read-side lookup of configured triggers across published versions."""

    def __init__(self, uow: UowFactory) -> None:
        self._uow = uow

    async def resolve(
        self,
        project_id: str,
        trigger_id: str,
        *,
        trigger_type: TriggerType | None = None,
    ) -> ResolvedTrigger | None:
        """Find ``trigger_id`` on the latest published version of
        ``project_id``. Loads that one version only; other projects' data can
        neither affect nor leak into the result."""
        async with self._uow() as uow:
            for pid, version in await uow.versions.list_latest(project_id=project_id):
                for trigger in version.triggers:
                    if trigger.id == trigger_id and (
                        trigger_type is None or trigger.trigger_type is trigger_type
                    ):
                        return ResolvedTrigger(pid, version.version, trigger)
        return None

    async def list_scheduled(self) -> list[ResolvedTrigger]:
        """All enabled CRON/INTERVAL triggers on latest published versions,
        in a deterministic order (AGENTS §72). The same trigger id may appear
        in several projects."""
        async with self._uow() as uow:
            latest = await uow.versions.list_latest()
        found = [
            ResolvedTrigger(project_id, version.version, trigger)
            for project_id, version in latest
            for trigger in version.triggers
            if trigger.enabled and trigger.trigger_type in SCHEDULED_TYPES
        ]
        return sorted(found, key=lambda r: (r.trigger.id, r.project_id))


class TriggerService:
    """The single funnel every trigger mechanism lands in (§32)."""

    def __init__(self, executions: ExecutionService) -> None:
        self._executions = executions

    async def fire(
        self, resolved: ResolvedTrigger, context: TriggerContext
    ) -> Execution:
        """Fire a configured trigger: enabled-check, then converge."""
        trigger = resolved.trigger
        if not trigger.enabled:
            raise TriggerDisabledError(
                "Trigger is disabled.", details={"trigger_id": trigger.id}
            )
        if context.trigger_type is not trigger.trigger_type:
            # A mechanism must only fire triggers of its own type; reaching
            # this is a wiring bug, not user data.
            raise DomainValidationError(
                "trigger context does not match the configured trigger type",
                details={
                    "trigger_id": trigger.id,
                    "trigger_type": trigger.trigger_type.value,
                    "context_type": context.trigger_type.value,
                },
            )
        return await self._create(
            resolved.project_id, resolved.version, context, trigger_id=trigger.id
        )

    async def fire_manual(
        self,
        project_id: str,
        version: int,
        *,
        input: Mapping[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> Execution:
        """Ad-hoc manual run (architecture §126): no configured Trigger."""
        context = TriggerContext(
            trigger_type=TriggerType.MANUAL,
            payload=input or {},
            source="manual",
            metadata={"correlation_id": correlation_id} if correlation_id else {},
        )
        return await self._create(project_id, version, context, trigger_id=None)

    async def _create(
        self,
        project_id: str,
        version: int,
        context: TriggerContext,
        *,
        trigger_id: str | None,
    ) -> Execution:
        execution = await self._executions.create_execution(
            project_id,
            version,
            input=context.payload,
            metadata=ExecutionMetadata(
                trigger_type=context.trigger_type,
                trigger_id=trigger_id,
                # Scheduled runs have no inbound request: they get their
                # own correlation root so their logs still group.
                correlation_id=context.metadata.get("correlation_id") or new_id("corr"),
            ),
        )
        logger.info(
            "trigger_fired",
            extra={
                "event": "trigger_fired",
                "execution_id": execution.id,
                "project_id": project_id,
                "project_version": version,
                "trigger_id": trigger_id,
                "trigger_type": context.trigger_type.value,
                "trigger_source": context.source,
                "correlation_id": execution.metadata.correlation_id,
            },
        )
        return execution
