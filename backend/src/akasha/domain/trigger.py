"""Trigger domain model: HOW an execution gets started.

Trigger types are fixed by the architecture (§17). Construction enforces only
the structural config rules that every persisted trigger has always
satisfied. The stricter schedule rules (``schedule_problem``) are validation
issues that block publishing, because drafts stored under older rules must
stay loadable so they can be fixed. Dependency constraints follow the same
rule (``validate_dependencies``).

``TriggerContext`` is the normalized shape every mechanism produces; the
runtime never sees mechanism specifics (architecture §17). Firing lives in
``akasha.application.triggers``, the scheduler in ``akasha.runtime.scheduler``.
"""

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from akasha.domain.cron import cron_problem
from akasha.domain.event import EventType
from akasha.domain.primitives import Entity, new_id, utcnow, validate_id
from akasha.errors import DomainValidationError

# Longest accepted interval. Calendar-scale schedules belong in cron.
MAX_INTERVAL_SECONDS = 365 * 24 * 60 * 60
# Deepest accepted payload nesting. Much deeper JSON makes deepcopy raise
# RecursionError (about 300 levels) and is never a sane trigger payload.
# ponytail: fixed cap; make it configurable with the Phase 24 resource limits.
MAX_PAYLOAD_DEPTH = 64
# WEBHOOK config key: the credential reference (``credential:<name>``) whose
# value signs requests (HMAC-SHA256 of the raw body, ``X-Akasha-Signature:
# sha256=<hex>``). Publishing requires it; firing verifies it.
WEBHOOK_SIGNING_FIELD = "secret_credential"
# EVENT config key: the project whose events fire the trigger. Absent: the
# trigger's own project. Firing requires the trigger project's owner to
# have access to it (``akasha.runtime.event_triggers``).
EVENT_SOURCE_FIELD = "source_project_id"
# Events an EVENT trigger may react to: outcomes of a whole execution, and
# publishing. Node-level events are too frequent and too internal to be a
# contract for other projects.
TRIGGERABLE_EVENTS: frozenset[EventType] = frozenset(
    {
        EventType.EXECUTION_COMPLETED,
        EventType.EXECUTION_FAILED,
        EventType.EXECUTION_CANCELLED,
        EventType.EXECUTION_TIMED_OUT,
        EventType.PROJECT_PUBLISHED,
    }
)


class TriggerType(StrEnum):
    MANUAL = "manual"
    API = "api"
    CRON = "cron"
    INTERVAL = "interval"
    WEBHOOK = "webhook"
    EVENT = "event"


@dataclass(eq=False)
class Trigger(Entity):
    """A configured trigger attached to a project draft/version."""

    trigger_type: TriggerType
    config: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True

    def __post_init__(self) -> None:
        self.id = validate_id(self.id)
        self.trigger_type = TriggerType(self.trigger_type)
        self._validate_config()

    def _validate_config(self) -> None:
        match self.trigger_type:
            case TriggerType.CRON:
                cron = self.config.get("cron")
                if not isinstance(cron, str) or not cron.strip():
                    raise DomainValidationError(
                        "cron trigger requires a non-empty 'cron' expression"
                    )
            case TriggerType.INTERVAL:
                seconds = self.config.get("interval_seconds")
                if (
                    not isinstance(seconds, (int, float))
                    or isinstance(seconds, bool)
                    # NaN/infinity can never be persisted (JSONB rejects
                    # them), so rejecting them here breaks no stored draft.
                    or (isinstance(seconds, float) and not math.isfinite(seconds))
                    or seconds <= 0
                ):
                    raise DomainValidationError(
                        "interval trigger requires positive, finite 'interval_seconds'"
                    )
            case TriggerType.EVENT:
                event = self.config.get("event")
                if not isinstance(event, str) or not event.strip():
                    raise DomainValidationError(
                        "event trigger requires a non-empty 'event' name"
                    )
            case TriggerType.MANUAL | TriggerType.API | TriggerType.WEBHOOK:
                # No mandatory config for these types yet.
                pass

    @classmethod
    def create(
        cls, trigger_type: TriggerType, config: dict[str, Any] | None = None
    ) -> "Trigger":
        return cls(id=new_id("trig"), trigger_type=trigger_type, config=config or {})


def event_problem(trigger: Trigger) -> str | None:
    """Why an EVENT trigger can never fire, or None. Checked at
    validate/publish time, like ``schedule_problem``; messages carry no
    config values."""
    if trigger.trigger_type is not TriggerType.EVENT:
        return None
    if trigger.config["event"] not in TRIGGERABLE_EVENTS:
        names = ", ".join(sorted(TRIGGERABLE_EVENTS))
        return f"'event' must be one of: {names}"
    source = trigger.config.get(EVENT_SOURCE_FIELD)
    if source is not None and not _is_id(source):
        return f"'{EVENT_SOURCE_FIELD}' must be a project id"
    return None


def _is_id(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("proj_"):
        return False
    try:
        validate_id(value)
    except DomainValidationError:
        return False
    return True


def schedule_problem(trigger: Trigger) -> str | None:
    """Why a CRON/INTERVAL trigger cannot be scheduled, or None if it can.

    Checked at validate/publish time, not at construction (see module
    docstring). Messages carry no config values.
    """
    match trigger.trigger_type:
        case TriggerType.CRON:
            return cron_problem(trigger.config["cron"])
        case TriggerType.INTERVAL:
            # The 1 s floor is the scheduler's tick granularity; the ceiling
            # keeps the due-time arithmetic far from datetime's range.
            if not 1 <= trigger.config["interval_seconds"] <= MAX_INTERVAL_SECONDS:
                return (
                    f"'interval_seconds' must be between 1 and {MAX_INTERVAL_SECONDS}"
                )
            return None
        case _:
            return None


@dataclass(frozen=True)
class TriggerContext:
    """The one shape every trigger mechanism normalizes into (§17).

    ``payload`` becomes the execution input: a JSON object nested at most
    ``MAX_PAYLOAD_DEPTH`` deep. ``headers`` are request metadata for
    validation and observability at the boundary. They are never persisted,
    since headers may carry secrets. ``metadata`` carries optional mechanism
    extras such as ``correlation_id``. ``source`` names the mechanism
    (``manual`` / ``api`` / ``webhook`` / ``schedule`` / ``event``).
    ``depth`` counts the event-trigger hops that led here: 0 for anything
    not started by an event trigger (loop protection, architecture §130).
    """

    trigger_type: TriggerType
    timestamp: datetime = field(default_factory=utcnow)
    payload: Mapping[str, Any] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)
    source: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)
    depth: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.depth, bool) or self.depth < 0:
            raise DomainValidationError("trigger depth must be >= 0")
        # dataclass is frozen: normalize via object.__setattr__.
        object.__setattr__(self, "trigger_type", TriggerType(self.trigger_type))
        if self.timestamp.tzinfo is None:
            raise DomainValidationError("trigger timestamp must be timezone-aware")
        object.__setattr__(self, "payload", dict(self.payload))
        object.__setattr__(self, "headers", dict(self.headers))
        object.__setattr__(self, "metadata", dict(self.metadata))
        if not _nesting_within(self.payload, MAX_PAYLOAD_DEPTH):
            raise DomainValidationError(
                "trigger payload is nested too deeply",
                details={"max_depth": MAX_PAYLOAD_DEPTH},
            )


def _nesting_within(value: object, limit: int) -> bool:
    """True when dicts/lists nest at most ``limit`` deep. Iterative, because
    the input may be deep enough to overflow a recursive walk."""
    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        children: Iterable[object]
        if isinstance(item, dict):
            children = item.values()
        elif isinstance(item, list):
            children = item
        else:
            continue
        if depth > limit:
            return False
        stack.extend((child, depth + 1) for child in children)
    return True
