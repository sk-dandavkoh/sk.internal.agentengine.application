"""Trigger domain model: HOW an execution gets started.

Trigger types are fixed by the architecture (vision §13); config invariants
live here so a misconfigured trigger fails at construction, not at fire time.
Scheduling itself is a later phase (10).
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from akasha.domain.primitives import Entity, new_id, validate_id
from akasha.errors import DomainValidationError


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
                    or seconds <= 0
                ):
                    raise DomainValidationError(
                        "interval trigger requires positive 'interval_seconds'"
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
