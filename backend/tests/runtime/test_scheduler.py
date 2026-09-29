"""TriggerScheduler: deterministic scheduling without a single real sleep
(backend/AGENTS.md §62/§63). The clock and sleep function are injected;
tests advance a fake clock and drive ``tick()`` directly.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from akasha.application.triggers import ResolvedTrigger, TriggerRegistry, TriggerService
from akasha.domain.execution import Execution
from akasha.domain.primitives import new_id
from akasha.domain.trigger import Trigger, TriggerContext, TriggerType
from akasha.runtime.scheduler import TriggerScheduler

START = datetime(2026, 1, 5, 12, 0, 30, tzinfo=UTC)  # half past a minute
PROJECT = "proj_" + "a" * 32


class FakeClock:
    def __init__(self, now: datetime = START) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


class FakeRegistry(TriggerRegistry):
    def __init__(self, schedules: list[ResolvedTrigger]) -> None:
        self.schedules = schedules
        self.calls = 0

    async def list_scheduled(self) -> list[ResolvedTrigger]:
        self.calls += 1
        return list(self.schedules)


class FakeService(TriggerService):
    def __init__(self, failing: set[str] | None = None) -> None:
        self.fires: list[tuple[ResolvedTrigger, TriggerContext]] = []
        self.failing = failing or set()

    async def fire(
        self, resolved: ResolvedTrigger, context: TriggerContext
    ) -> Execution:
        if resolved.trigger.id in self.failing:
            raise RuntimeError("persistence down")
        self.fires.append((resolved, context))
        return cast(Execution, None)  # never read: the scheduler only counts


def scheduled(trigger: Trigger, project_id: str = PROJECT) -> ResolvedTrigger:
    return ResolvedTrigger(project_id, 1, trigger)


def cron_trigger(expr: str = "*/2 * * * *") -> Trigger:
    return Trigger.create(TriggerType.CRON, {"cron": expr})


def interval_trigger(seconds: float = 30) -> Trigger:
    return Trigger.create(TriggerType.INTERVAL, {"interval_seconds": seconds})


def make(clock: FakeClock, trigger: Trigger) -> tuple[TriggerScheduler, FakeService]:
    registry = FakeRegistry([scheduled(trigger)])
    service = FakeService()
    return TriggerScheduler(registry, service, clock=clock), service


class TestInterval:
    async def test_arms_first_then_fires_per_boundary(self) -> None:
        clock = FakeClock()
        trigger = interval_trigger(30)
        scheduler, service = make(clock, trigger)

        assert await scheduler.tick() == 0  # first tick only arms, never retro-fires
        clock.advance(seconds=29)
        assert await scheduler.tick() == 0
        clock.advance(seconds=1)
        assert await scheduler.tick() == 1
        clock.advance(seconds=30)
        assert await scheduler.tick() == 1

        _, context = service.fires[0]
        assert context.trigger_type is TriggerType.INTERVAL
        assert context.source == "schedule"
        assert context.timestamp == START + timedelta(seconds=30)
        assert context.payload == {}

    async def test_missed_intervals_are_skipped_not_replayed(self) -> None:
        clock = FakeClock()
        scheduler, _ = make(clock, interval_trigger(30))

        await scheduler.tick()
        clock.advance(seconds=4000)  # ~133 boundaries flew by
        assert await scheduler.tick() == 1  # exactly one catch-up fire
        # Next boundary re-anchored ahead: due=START+30 → next=START+4020.
        clock.advance(seconds=19)
        assert await scheduler.tick() == 0
        clock.advance(seconds=11)  # now ≥ START+4020: schedule resumes
        assert await scheduler.tick() == 1


class TestCron:
    async def test_fires_at_cron_boundaries_only(self) -> None:
        clock = FakeClock()
        scheduler, service = make(clock, cron_trigger("*/2 * * * *"))

        assert await scheduler.tick() == 0  # armed; next is 12:02:00
        clock.advance(minutes=1, seconds=29)  # 12:01:59
        assert await scheduler.tick() == 0
        clock.advance(seconds=1)  # 12:02:00
        assert await scheduler.tick() == 1
        clock.advance(minutes=1, seconds=59)  # 12:03:59
        assert await scheduler.tick() == 0
        clock.advance(seconds=1)  # 12:04:00
        assert await scheduler.tick() == 1
        assert len(service.fires) == 2

    async def test_downtime_fires_once_not_per_missed_boundary(self) -> None:
        clock = FakeClock()
        scheduler, service = make(clock, cron_trigger("* * * * *"))

        await scheduler.tick()
        clock.advance(hours=6)
        assert await scheduler.tick() == 1
        assert len(service.fires) == 1


class TestLifecycleOfSchedules:
    async def test_removed_trigger_state_is_pruned_and_rearmed(self) -> None:
        clock = FakeClock()
        trigger = interval_trigger(30)
        registry = FakeRegistry([scheduled(trigger)])
        service = FakeService()
        scheduler = TriggerScheduler(registry, service, clock=clock)

        await scheduler.tick()
        assert (PROJECT, trigger.id) in scheduler._due

        registry.schedules.clear()  # unpublished / disabled / deleted
        await scheduler.tick()
        assert scheduler._due == {}

        registry.schedules.append(scheduled(trigger))  # republished
        assert await scheduler.tick() == 0  # re-armed, no retro-fire

    async def test_changed_schedule_rearms_from_now(self) -> None:
        # A republish that changes the schedule takes effect at once instead
        # of after the boundary computed for the old configuration.
        clock = FakeClock()
        trigger = interval_trigger(30)
        registry = FakeRegistry([scheduled(trigger)])
        service = FakeService()
        scheduler = TriggerScheduler(registry, service, clock=clock)
        await scheduler.tick()

        clock.advance(seconds=20)
        republished = Trigger(
            id=trigger.id,
            trigger_type=TriggerType.INTERVAL,
            config={"interval_seconds": 60},
        )
        registry.schedules = [scheduled(republished)]
        assert await scheduler.tick() == 0  # re-armed: due at +20 s + 60 s
        clock.advance(seconds=30)  # the old boundary (+30 s) has passed
        assert await scheduler.tick() == 0
        clock.advance(seconds=30)  # +80 s: new boundary
        assert await scheduler.tick() == 1

    async def test_failed_fire_does_not_break_the_pass(self) -> None:
        clock = FakeClock()
        bad, good = interval_trigger(30), interval_trigger(30)
        registry = FakeRegistry([scheduled(bad), scheduled(good)])
        service = FakeService(failing={bad.id})
        scheduler = TriggerScheduler(registry, service, clock=clock)

        await scheduler.tick()
        clock.advance(seconds=30)
        assert await scheduler.tick() == 1  # only the healthy one counts
        assert [r.trigger.id for r, _ in service.fires] == [good.id]

        clock.advance(seconds=30)
        assert await scheduler.tick() == 1  # keeps firing on schedule


class TestIsolation:
    """One trigger's problem never starves the others (§99)."""

    @pytest.mark.parametrize(
        ("trigger_type", "config"),
        [
            # Configurations publish validation rejects; the scheduler must
            # still survive them (e.g. versions published before the rules).
            (TriggerType.INTERVAL, {"interval_seconds": 1e12}),
            (TriggerType.CRON, {"cron": "0 0 31 2 *"}),
            (TriggerType.CRON, {"cron": "not a cron"}),
        ],
    )
    async def test_unschedulable_trigger_is_parked_and_logged_once(
        self,
        trigger_type: TriggerType,
        config: dict[str, object],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        clock = FakeClock()
        bad = Trigger(id="trig_" + "0" * 32, trigger_type=trigger_type, config=config)
        good = Trigger(
            id="trig_" + "f" * 32,
            trigger_type=TriggerType.INTERVAL,
            config={"interval_seconds": 30},
        )
        service = FakeService()
        # The bad trigger sorts first: it used to abort the whole pass.
        scheduler = TriggerScheduler(
            FakeRegistry([scheduled(bad), scheduled(good)]), service, clock=clock
        )

        with caplog.at_level(logging.ERROR, logger="akasha.runtime.scheduler"):
            fired = []
            for _ in range(4):
                fired.append(await scheduler.tick())
                clock.advance(seconds=30)

        assert fired == [0, 1, 1, 1]  # the healthy trigger keeps its schedule
        assert {r.trigger.id for r, _ in service.fires} == {good.id}
        failures = [
            r for r in caplog.records if r.getMessage() == "scheduled_trigger_failed"
        ]
        assert len(failures) == 1  # parked: no log line on every tick
        assert failures[0].__dict__["trigger_id"] == bad.id

    async def test_type_change_under_same_id_rearms(self) -> None:
        clock = FakeClock()
        tid = new_id("trig")
        registry = FakeRegistry(
            [
                scheduled(
                    Trigger(
                        id=tid,
                        trigger_type=TriggerType.CRON,
                        config={"cron": "* * * * *"},
                    )
                )
            ]
        )
        service = FakeService()
        scheduler = TriggerScheduler(registry, service, clock=clock)
        await scheduler.tick()  # armed as CRON

        registry.schedules = [
            scheduled(
                Trigger(
                    id=tid,
                    trigger_type=TriggerType.INTERVAL,
                    config={"interval_seconds": 30},
                )
            )
        ]
        clock.advance(seconds=31)
        assert await scheduler.tick() == 0  # re-armed as INTERVAL, no KeyError
        clock.advance(seconds=30)
        assert await scheduler.tick() == 1
        assert service.fires[0][1].trigger_type is TriggerType.INTERVAL

    async def test_same_trigger_id_in_two_projects_fires_both(self) -> None:
        clock = FakeClock()
        tid = new_id("trig")
        other_project = "proj_" + "b" * 32
        config = {"interval_seconds": 30}
        registry = FakeRegistry(
            [
                scheduled(Trigger(tid, TriggerType.INTERVAL, dict(config))),
                scheduled(
                    Trigger(tid, TriggerType.INTERVAL, dict(config)), other_project
                ),
            ]
        )
        service = FakeService()
        scheduler = TriggerScheduler(registry, service, clock=clock)

        await scheduler.tick()
        clock.advance(seconds=30)
        assert await scheduler.tick() == 2
        assert sorted(r.project_id for r, _ in service.fires) == [
            PROJECT,
            other_project,
        ]


class TestRunLoop:
    async def test_runs_until_cancelled_via_sleep(self) -> None:
        clock = FakeClock()
        registry = FakeRegistry([scheduled(interval_trigger(30))])
        service = FakeService()
        sleeps = 0

        async def fake_sleep(_: float) -> None:
            nonlocal sleeps
            sleeps += 1
            if sleeps == 2:
                raise asyncio.CancelledError

        scheduler = TriggerScheduler(registry, service, clock=clock, sleep=fake_sleep)
        with pytest.raises(asyncio.CancelledError):
            await scheduler.run()
        assert registry.calls == 2  # two real ticks, zero real sleeping

    async def test_survives_a_broken_tick(self) -> None:
        clock = FakeClock()

        class FlakyRegistry(FakeRegistry):
            async def list_scheduled(self) -> list[ResolvedTrigger]:
                if self.calls == 0:
                    self.calls += 1
                    raise RuntimeError("db gone")
                return await super().list_scheduled()

        ticks = 0

        async def fake_sleep(_: float) -> None:
            nonlocal ticks
            ticks += 1
            if ticks == 2:
                raise asyncio.CancelledError

        scheduler = TriggerScheduler(
            FlakyRegistry([scheduled(interval_trigger(30))]),
            FakeService(),
            clock=clock,
            sleep=fake_sleep,
        )
        with pytest.raises(asyncio.CancelledError):
            await scheduler.run()
        assert ticks == 2  # the failure cost one tick, not the loop

    def test_rejects_non_positive_poll(self) -> None:
        with pytest.raises(ValueError, match="poll_seconds"):
            TriggerScheduler(FakeRegistry([]), FakeService(), poll_seconds=0)
