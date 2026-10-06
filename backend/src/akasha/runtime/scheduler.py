"""In-process scheduler for CRON/INTERVAL triggers (roadmap Phase 10).

It runs as one asyncio task per API process, owned by the ``Container``.
It never runs a graph: a fire goes
through ``TriggerService`` and ends in the Execution Service, like every
other mechanism (backend/AGENTS §32, architecture §128).

Deterministic by construction (backend/AGENTS §60/§62/§63): the clock and the
sleep function are injectable, and ``tick()`` does one full scheduling pass,
so tests advance a fake clock and never sleep.

Missed runs are skipped: after downtime, the next due time is the first
boundary after *now*, so there is no catch-up storm. Each trigger is handled
in isolation. If one trigger fails, the error is logged and the pass goes on
with the others (§99).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from akasha.application.triggers import ResolvedTrigger, TriggerRegistry, TriggerService
from akasha.domain.cron import next_fire
from akasha.domain.trigger import Trigger, TriggerContext, TriggerType

logger = logging.getLogger("akasha.runtime.scheduler")

Clock = Callable[[], datetime]
Sleep = Callable[[float], Awaitable[None]]


def utc_now() -> datetime:
    return datetime.now(UTC)


class TriggerScheduler:
    def __init__(
        self,
        registry: TriggerRegistry,
        service: TriggerService,
        *,
        clock: Clock = utc_now,
        poll_seconds: float = 1.0,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be > 0")
        self._registry = registry
        self._service = service
        self._clock = clock
        self._poll_seconds = poll_seconds
        self._sleep = sleep
        # (workflow id, trigger id) -> (schedule spec, next due). Trigger ids
        # are unique only per workflow. The spec makes a republished trigger
        # with a changed type or schedule re-arm from scratch. A next due of
        # None parks an unschedulable trigger until its spec changes. The
        # state lives in memory: a restart re-anchors every schedule.
        self._due: dict[tuple[str, str], tuple[str, datetime | None]] = {}

    async def tick(self) -> int:
        """One scheduling pass against ``clock()``; returns fires started."""
        now = self._clock()
        schedules = await self._registry.list_scheduled()
        live = {(r.workflow_id, r.trigger.id) for r in schedules}
        # Drop the state of triggers that were unpublished or disabled.
        self._due = {key: state for key, state in self._due.items() if key in live}
        fired = 0
        for resolved in schedules:
            try:
                if await self._step(resolved, now):
                    fired += 1
            except Exception:
                # One broken trigger must never starve the others (§99).
                # _step has already advanced or parked the trigger's state,
                # so this is logged once per boundary or configuration, not
                # on every tick.
                logger.exception(
                    "scheduled_trigger_failed",
                    extra={
                        "event": "scheduled_trigger_failed",
                        "trigger_id": resolved.trigger.id,
                        "workflow_id": resolved.workflow_id,
                        "workflow_version": resolved.version,
                    },
                )
        return fired

    async def _step(self, resolved: ResolvedTrigger, now: datetime) -> bool:
        """Arm, or fire when due. True when an execution was created."""
        trigger = resolved.trigger
        key = (resolved.workflow_id, trigger.id)
        spec = _schedule_spec(trigger)
        state = self._due.get(key)
        if state is None or state[0] != spec:
            # First sight, or a changed schedule: (re-)arm without firing,
            # so a trigger never fires retroactively. Park the trigger first,
            # so that a schedule that cannot be computed stays parked.
            self._due[key] = (spec, None)
            self._due[key] = (spec, _first_due(trigger, now))
            return False
        due = state[1]
        if due is None or now < due:
            return False
        # Advance (or park) before firing, so a failing fire is not retried
        # on every tick.
        self._due[key] = (spec, None)
        self._due[key] = (spec, _next_due(trigger, due, now))
        await self._service.fire(
            resolved,
            TriggerContext(
                trigger_type=trigger.trigger_type, timestamp=now, source="schedule"
            ),
        )
        return True

    async def run(self) -> None:
        """Poll loop. Cancellation (owner: Container) is the clean stop.

        A failed tick, e.g. a transient DB error in ``list_scheduled``, is
        logged and retried at the next poll. The scheduler must not die
        silently and leave executions permanently invisible (§45/§99).
        """
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception(
                    "trigger_tick_failed", extra={"event": "trigger_tick_failed"}
                )
            await self._sleep(self._poll_seconds)


def _schedule_spec(trigger: Trigger) -> str:
    if trigger.trigger_type is TriggerType.CRON:
        return f"cron {trigger.config['cron']}"
    return f"interval {trigger.config['interval_seconds']!r}"


def _interval(trigger: Trigger) -> timedelta:
    return timedelta(seconds=float(trigger.config["interval_seconds"]))


def _first_due(trigger: Trigger, now: datetime) -> datetime:
    if trigger.trigger_type is TriggerType.CRON:
        return next_fire(trigger.config["cron"], now)
    return now + _interval(trigger)


def _next_due(trigger: Trigger, due: datetime, now: datetime) -> datetime:
    """The first boundary after ``now``; missed boundaries are skipped."""
    if trigger.trigger_type is TriggerType.CRON:
        return next_fire(trigger.config["cron"], now)
    interval = _interval(trigger)
    # O(1) jump; stays on the grid anchored at the first due time.
    return due + (int((now - due) // interval) + 1) * interval
