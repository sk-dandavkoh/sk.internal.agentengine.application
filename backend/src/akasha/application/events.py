"""In-process event fan-out (roadmap Phase 11, architecture §19).

Delivery contract:
- Events are published only *after* the transaction that stored them
  committed, so no subscriber ever sees an event whose state was rolled back.
- Delivery is at-most-once and best effort: a process crash between commit
  and publish loses the live notification, never the event. The persisted
  event log (``EventRepository.list``) is the authoritative history;
  consumers that must not miss anything catch up from it by position.
- Subscribers must be idempotent on ``event_id`` (a catch-up read and a live
  delivery can overlap).
- A failing or hanging subscriber is isolated: it is logged and skipped. It
  can never fail, roll back or block the use case that produced the event —
  domain state does not depend on consumers.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Sequence

from akasha.domain.event import Event, EventType
from akasha.errors import ConfigurationError

logger = logging.getLogger("akasha.application.events")

EventSubscriber = Callable[[Event], Awaitable[None]]


class EventPublisher:
    def __init__(self, *, subscriber_timeout: float = 5.0) -> None:
        if subscriber_timeout <= 0:
            raise ConfigurationError("subscriber_timeout must be positive")
        self._timeout = subscriber_timeout
        self._subscriptions: list[
            tuple[frozenset[EventType] | None, EventSubscriber]
        ] = []

    def subscribe(
        self,
        subscriber: EventSubscriber,
        *,
        event_types: Iterable[EventType] | None = None,
    ) -> Callable[[], None]:
        """Register ``subscriber`` (all types when ``event_types`` is None);
        returns the matching unsubscribe function."""
        entry = (None if event_types is None else frozenset(event_types), subscriber)
        self._subscriptions.append(entry)
        return lambda: self._subscriptions.remove(entry)

    async def publish(self, events: Sequence[Event]) -> None:
        """Deliver in order, each event to each matching subscriber in
        subscription order.

        ponytail: subscribers run inline, so a slow one delays the producer
        by up to ``subscriber_timeout`` per event; live-update consumers
        should only enqueue. Move to a per-subscriber queue task when a real
        consumer needs to do I/O here.
        """
        for event in events:
            for types, subscriber in list(self._subscriptions):
                if types is not None and event.event_type not in types:
                    continue
                try:
                    async with asyncio.timeout(self._timeout):
                        await subscriber(event)
                # Deliberately broad: a consumer bug must never propagate into
                # the runtime or a request (module docstring). CancelledError
                # is a BaseException and still propagates.
                except Exception as exc:
                    logger.error(
                        "event_subscriber_failed",
                        exc_info=exc,
                        extra={
                            "event": "event_subscriber_failed",
                            "event_id": event.event_id,
                            "event_type": event.event_type.value,
                            "execution_id": event.execution_id,
                            "correlation_id": event.correlation_id,
                            "subscriber": getattr(
                                subscriber, "__qualname__", type(subscriber).__name__
                            ),
                        },
                    )
