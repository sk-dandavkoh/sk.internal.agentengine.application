"""Event: an immutable record of something that happened (architecture §18).

Events describe state changes; they never *are* the state. The Execution and
Project aggregates stay canonical — events are the history next to them, used
for monitoring, live updates and audit.

Payloads are closed per event type: every type declares exactly which keys it
carries, and values are scalars (ids, statuses, error codes, counters). That
is a security rule, not tidiness: node inputs/outputs, config values and
free-text reasons may hold business data or secrets (AGENTS §76/§77), so an
event can structurally not carry them.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final

from akasha.domain.primitives import new_id, validate_id
from akasha.errors import DomainValidationError

# Bump when an event type's payload changes meaning; consumers check it
# (AGENTS §81). Adding a new event type does not require a bump.
EVENT_SCHEMA_VERSION: Final = 1


class EventType(StrEnum):
    EXECUTION_CREATED = "execution.created"
    EXECUTION_STARTED = "execution.started"
    EXECUTION_COMPLETED = "execution.completed"
    EXECUTION_FAILED = "execution.failed"
    EXECUTION_CANCELLED = "execution.cancelled"
    EXECUTION_TIMED_OUT = "execution.timed_out"
    NODE_STARTED = "node.started"
    NODE_COMPLETED = "node.completed"
    NODE_FAILED = "node.failed"
    NODE_SKIPPED = "node.skipped"
    NODE_CANCELLED = "node.cancelled"
    NODE_EMITTED = "node.emitted"  # a node's own declared event (SDK)
    PROJECT_PUBLISHED = "project.published"


_ERROR = frozenset({"error_code", "error_kind"})

# event type -> exact payload keys. Scope follows from the type's prefix:
# execution.* and node.* need an execution, node.* also a node.
PAYLOAD_KEYS: Final[Mapping[EventType, frozenset[str]]] = {
    EventType.EXECUTION_CREATED: frozenset({"trigger_type", "trigger_id"}),
    EventType.EXECUTION_STARTED: frozenset(),
    EventType.EXECUTION_COMPLETED: frozenset(),
    EventType.EXECUTION_FAILED: _ERROR | {"error_node_id"},
    # No cancellation reason: it is caller-supplied free text.
    EventType.EXECUTION_CANCELLED: frozenset(),
    EventType.EXECUTION_TIMED_OUT: frozenset({"error_code"}),
    EventType.NODE_STARTED: frozenset({"attempt"}),
    EventType.NODE_COMPLETED: frozenset({"attempt"}),
    EventType.NODE_FAILED: _ERROR | {"attempt"},
    EventType.NODE_SKIPPED: frozenset(),
    EventType.NODE_CANCELLED: frozenset(),
    # ``name`` is one of the node definition's declared event names.
    EventType.NODE_EMITTED: frozenset({"name", "attempt"}),
    EventType.PROJECT_PUBLISHED: frozenset(),
}

type PayloadValue = str | int | None


@dataclass(frozen=True)
class Event:
    """``position`` is assigned by the event store (None until persisted);
    events of one execution (or one project) are totally ordered by it."""

    event_type: EventType
    occurred_at: datetime
    project_id: str
    execution_id: str | None = None
    node_id: str | None = None
    project_version: int | None = None
    correlation_id: str | None = None
    payload: Mapping[str, PayloadValue] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: new_id("evt"))
    schema_version: int = EVENT_SCHEMA_VERSION
    position: int | None = None

    def __post_init__(self) -> None:
        event_type = EventType(self.event_type)
        object.__setattr__(self, "event_type", event_type)
        validate_id(self.event_id)
        validate_id(self.project_id)
        if self.occurred_at.tzinfo is None:
            raise DomainValidationError("event time must be timezone-aware")
        scope = event_type.value.split(".", 1)[0]
        if (self.execution_id is None) == (scope in ("execution", "node")):
            raise DomainValidationError(f"{event_type} requires exactly its scope ids")
        if (self.node_id is None) == (scope == "node"):
            raise DomainValidationError(f"{event_type} requires exactly its scope ids")
        for value in (self.execution_id, self.node_id):
            if value is not None:
                validate_id(value)
        payload = dict(self.payload)  # defensive copy: events are immutable
        if payload.keys() != PAYLOAD_KEYS[event_type]:
            raise DomainValidationError(
                f"invalid payload keys for {event_type}",
                details={"keys": ",".join(sorted(payload))},
            )
        # bool is an int subclass; reject it so the schema stays honest.
        if any(
            isinstance(v, bool) or not isinstance(v, str | int | None)
            for v in payload.values()
        ):
            raise DomainValidationError("event payload values must be str, int or None")
        object.__setattr__(self, "payload", payload)
        if self.schema_version < 1:
            raise DomainValidationError("schema_version must be >= 1")
