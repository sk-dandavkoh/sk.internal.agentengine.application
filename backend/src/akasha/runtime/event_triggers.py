"""Dispatcher for EVENT triggers (architecture §129/§130).

Runs next to the cron scheduler, one asyncio task owned by the
``Container``, exactly one replica. It reads the persisted event log, not
the in-process ``EventPublisher``: executions run in worker processes, whose
events this process never sees live (the same reason ``api/live.py`` reads
the log). A fire goes through ``TriggerService``, like every mechanism.

Delivery:
- **No catch-up.** The first tick anchors at the end of the log, so a
  restart neither replays history nor fires twice. Events stored while no
  dispatcher ran are not dispatched, like missed cron boundaries (§125).
- **Gaps.** Positions come from an identity column, which is assigned at
  insert, not at commit. A slower transaction can commit position 10 after
  11 was read. Every position skipped by a read is remembered and re-read
  until it shows up or ``gap_timeout`` passes. Rolled-back transactions
  leave such gaps forever.
- **At most once per event and trigger.** A failed fire (resource limit,
  queue down) is logged, not retried, so a retry storm cannot start here.

Loop protection (§130/§131): an execution started by an event trigger
records ``trigger_depth`` = the source execution's depth + 1. A fire that
would exceed ``max_depth`` is refused and logged, so a project that triggers
itself on its own completion stops after ``max_depth`` runs.

Authorization: a trigger reacts to events of another project only when the
trigger project's owner may access that project, by the same rule as the
API (``may_access_project``): admins every project, members their own.
Events must not let one user observe or chain onto another user's projects
(§49). Projects without an owner (operator CLI) may chain among themselves:
both are admin-only, so no access widens. The check runs at fire time, so a
demoted or disabled owner, or a deleted source, takes effect immediately.
"""

import asyncio
import logging
from collections.abc import Sequence
from datetime import datetime, timedelta

from akasha.application.persistence import UnitOfWork
from akasha.application.projects import UowFactory
from akasha.application.triggers import ResolvedTrigger, TriggerRegistry, TriggerService
from akasha.domain.event import Event
from akasha.domain.identity import may_access_project
from akasha.domain.project import Project
from akasha.domain.trigger import (
    EVENT_SOURCE_FIELD,
    TRIGGERABLE_EVENTS,
    TriggerContext,
    TriggerType,
)
from akasha.runtime.scheduler import Clock, Sleep, utc_now

logger = logging.getLogger("akasha.runtime.event_triggers")

# ponytail: fixed caps; make them settings if a deployment needs to tune them.
BATCH_SIZE = 500
MAX_GAPS = 10_000


class EventTriggerDispatcher:
    def __init__(
        self,
        uow: UowFactory,
        registry: TriggerRegistry,
        service: TriggerService,
        *,
        max_depth: int,
        clock: Clock = utc_now,
        poll_seconds: float = 1.0,
        gap_timeout: timedelta = timedelta(seconds=60),
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if max_depth < 1:
            raise ValueError("max_depth must be >= 1")
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be > 0")
        self._uow = uow
        self._registry = registry
        self._service = service
        self._max_depth = max_depth
        self._clock = clock
        self._poll_seconds = poll_seconds
        self._gap_timeout = gap_timeout
        self._sleep = sleep
        # None until the first tick anchors at the end of the log.
        self._high: int | None = None
        # Skipped position -> when it was first missed.
        self._gaps: dict[int, datetime] = {}
        # True when the last tick read a full batch: more is waiting.
        self.behind = False

    async def tick(self) -> int:
        """Read new events (and missed ones that have since committed),
        fire the triggers they match; returns the fires started."""
        now = self._clock()
        async with self._uow() as uow:
            if self._high is None:
                self._high = await uow.events.max_position()
                return 0
            fresh = await uow.events.list(after=self._high, limit=BATCH_SIZE)
            filled = (
                await uow.events.list(positions=list(self._gaps), limit=len(self._gaps))
                if self._gaps
                else []
            )
        self.behind = len(fresh) == BATCH_SIZE
        expected = self._high + 1
        for event in fresh:
            position = event.position or 0
            for missing in range(expected, position):
                self._gaps.setdefault(missing, now)
            expected = position + 1
        if fresh:
            self._high = fresh[-1].position or self._high
        for event in filled:
            self._gaps.pop(event.position or 0, None)
        self._gaps = {
            p: seen for p, seen in self._gaps.items() if now - seen < self._gap_timeout
        }
        if len(self._gaps) > MAX_GAPS:
            # Oldest positions first: they are the least likely to commit.
            self._gaps = dict(sorted(self._gaps.items())[-MAX_GAPS:])
        candidates = sorted(
            (e for e in (*filled, *fresh) if e.event_type in TRIGGERABLE_EVENTS),
            key=lambda e: e.position or 0,
        )
        if not candidates:
            return 0
        triggers = await self._registry.list_event_triggers()
        fired = 0
        for event in candidates:
            fired += await self._dispatch(event, triggers)
        return fired

    async def _dispatch(self, event: Event, triggers: Sequence[ResolvedTrigger]) -> int:
        matches = [
            r
            for r in triggers
            if r.trigger.config.get("event") == event.event_type.value
            and r.trigger.config.get(EVENT_SOURCE_FIELD, r.project_id)
            == event.project_id
        ]
        if not matches:
            return 0
        async with self._uow() as uow:
            source = await uow.projects.get(event.project_id)
            depth = 1
            if event.execution_id is not None:
                execution = await uow.executions.get(event.execution_id)
                if execution is not None:
                    depth = execution.metadata.trigger_depth + 1
            # Trigger project -> may it react to the source (other projects).
            allowed: dict[str, bool] = {}
            for r in matches:
                if r.project_id not in allowed and r.project_id != event.project_id:
                    allowed[r.project_id] = await _may_observe(
                        uow, r.project_id, source
                    )
        fired = 0
        for resolved in matches:
            refusal = self._refusal(resolved, event, depth, allowed)
            if refusal is not None:
                logger.warning(
                    refusal,
                    extra=self._log_extra(refusal, resolved, event, depth),
                )
                continue
            try:
                await self._service.fire(resolved, self._context(event, depth))
                fired += 1
            except Exception:
                # One failing fire must not starve the others (§99); it is
                # not retried (module docstring).
                logger.exception(
                    "event_trigger_failed",
                    extra=self._log_extra(
                        "event_trigger_failed", resolved, event, depth
                    ),
                )
        return fired

    def _refusal(
        self,
        resolved: ResolvedTrigger,
        event: Event,
        depth: int,
        allowed: dict[str, bool],
    ) -> str | None:
        if depth > self._max_depth:
            return "event_trigger_depth_exceeded"
        if resolved.project_id != event.project_id and not allowed[resolved.project_id]:
            return "event_trigger_source_denied"
        return None

    @staticmethod
    def _context(event: Event, depth: int) -> TriggerContext:
        # Only the event's own fields: ids, statuses, error codes (event
        # payloads are closed and secret-free, domain/event.py).
        payload = {
            "event": {
                "type": event.event_type.value,
                "event_id": event.event_id,
                "project_id": event.project_id,
                "project_version": event.project_version,
                "execution_id": event.execution_id,
                "occurred_at": event.occurred_at.isoformat(),
                "data": dict(event.payload),
            }
        }
        return TriggerContext(
            trigger_type=TriggerType.EVENT,
            payload=payload,
            source="event",
            # The chain shares one correlation id, so its logs group.
            metadata=(
                {"correlation_id": event.correlation_id} if event.correlation_id else {}
            ),
            depth=depth,
        )

    @staticmethod
    def _log_extra(
        name: str, resolved: ResolvedTrigger, event: Event, depth: int
    ) -> dict[str, object]:
        return {
            "event": name,
            "trigger_id": resolved.trigger.id,
            "project_id": resolved.project_id,
            "project_version": resolved.version,
            "source_event_id": event.event_id,
            "source_event_type": event.event_type.value,
            "source_project_id": event.project_id,
            "trigger_depth": depth,
            "correlation_id": event.correlation_id,
        }

    async def run(self) -> None:
        """Poll loop; cancellation (owner: Container) is the clean stop. A
        failed tick is logged and retried at the next poll (§45/§99)."""
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception(
                    "event_trigger_tick_failed",
                    extra={"event": "event_trigger_tick_failed"},
                )
                self.behind = False
            if not self.behind:
                await self._sleep(self._poll_seconds)


async def _may_observe(
    uow: UnitOfWork, project_id: str, source: Project | None
) -> bool:
    """Whether project ``project_id``'s owner may react to ``source``."""
    project = await uow.projects.get(project_id)
    if source is None or project is None:
        return False
    if project.owner_id is None:  # an ownerless operator project
        return source.owner_id is None
    owner = await uow.users.get(project.owner_id)
    return (
        owner is not None
        and not owner.disabled
        and may_access_project(owner, source.owner_id)
    )
