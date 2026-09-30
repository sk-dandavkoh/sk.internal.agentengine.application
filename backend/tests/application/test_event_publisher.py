"""EventPublisher: ordering, filtering, subscription and consumer isolation."""

import asyncio
import logging
from datetime import UTC, datetime

import pytest

from akasha.application.events import EventPublisher
from akasha.domain.event import Event, EventType
from akasha.domain.primitives import new_id
from akasha.errors import ConfigurationError

EXEC = new_id("exec")


def evt(event_type: EventType = EventType.EXECUTION_STARTED) -> Event:
    return Event(
        event_type=event_type,
        occurred_at=datetime(2100, 1, 1, tzinfo=UTC),
        project_id=new_id("proj"),
        execution_id=EXEC,
        correlation_id="corr-7",
    )


class Recorder:
    def __init__(self) -> None:
        self.seen: list[Event] = []

    async def __call__(self, event: Event) -> None:
        self.seen.append(event)


async def test_publishes_every_event_in_order_to_every_subscriber() -> None:
    publisher = EventPublisher()
    first, second = Recorder(), Recorder()
    publisher.subscribe(first)
    publisher.subscribe(second)
    events = [evt(), evt(EventType.EXECUTION_COMPLETED)]

    await publisher.publish(events)

    assert first.seen == second.seen == events


async def test_subscription_filters_by_event_type() -> None:
    publisher = EventPublisher()
    terminal = Recorder()
    publisher.subscribe(terminal, event_types=[EventType.EXECUTION_COMPLETED])

    await publisher.publish([evt(), evt(EventType.EXECUTION_COMPLETED)])

    assert [e.event_type for e in terminal.seen] == [EventType.EXECUTION_COMPLETED]


async def test_unsubscribe_stops_delivery() -> None:
    publisher = EventPublisher()
    recorder = Recorder()
    unsubscribe = publisher.subscribe(recorder)
    await publisher.publish([evt()])
    unsubscribe()
    await publisher.publish([evt()])
    assert len(recorder.seen) == 1


async def test_subscriber_may_unsubscribe_itself_during_delivery() -> None:
    publisher = EventPublisher()
    after = Recorder()

    async def once(event: Event) -> None:
        unsubscribe()

    unsubscribe = publisher.subscribe(once)
    publisher.subscribe(after)
    await publisher.publish([evt(), evt()])
    assert len(after.seen) == 2


async def test_publishing_without_subscribers_is_a_no_op() -> None:
    await EventPublisher().publish([evt()])


async def test_failing_subscriber_is_isolated_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    publisher = EventPublisher()
    before, after = Recorder(), Recorder()

    async def broken(event: Event) -> None:
        raise RuntimeError("consumer bug")

    publisher.subscribe(before)
    publisher.subscribe(broken)
    publisher.subscribe(after)
    events = [evt(), evt(EventType.EXECUTION_COMPLETED)]

    with caplog.at_level(logging.ERROR, logger="akasha.application.events"):
        await publisher.publish(events)  # must not raise

    assert before.seen == after.seen == events
    failures = [
        r for r in caplog.records if r.getMessage() == "event_subscriber_failed"
    ]
    assert len(failures) == 2
    record = failures[0]
    assert record.__dict__["event_id"] == events[0].event_id
    assert record.__dict__["event_type"] == "execution.started"
    assert record.__dict__["correlation_id"] == "corr-7"
    assert "broken" in record.__dict__["subscriber"]


async def test_hanging_subscriber_times_out_and_delivery_continues() -> None:
    publisher = EventPublisher(subscriber_timeout=0.01)
    after = Recorder()

    async def hangs(event: Event) -> None:
        await asyncio.Event().wait()

    publisher.subscribe(hangs)
    publisher.subscribe(after)

    async with asyncio.timeout(5):  # guard: a hang fails instead of blocking CI
        await publisher.publish([evt()])

    assert len(after.seen) == 1


async def test_cancellation_of_the_publisher_is_not_swallowed() -> None:
    publisher = EventPublisher()
    entered = asyncio.Event()

    async def slow(event: Event) -> None:
        entered.set()
        await asyncio.Event().wait()

    publisher.subscribe(slow)
    task = asyncio.create_task(publisher.publish([evt()]))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_subscriber_timeout_must_be_positive() -> None:
    with pytest.raises(ConfigurationError):
        EventPublisher(subscriber_timeout=0)
