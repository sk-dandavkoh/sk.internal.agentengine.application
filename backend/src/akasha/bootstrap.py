"""Application composition: single, explicit build point for dependencies.

The Container is created once at startup and stored on ``app.state``. No
global singletons — tests build their own Container and FastAPI app via
``create_container`` / ``create_app``.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from akasha.config import Settings

# A readiness check returns None when healthy, or a human-readable reason.
ReadyCheck = Callable[[], Awaitable[str | None]]


@dataclass
class Container:
    """Composition root. Later phases register repositories/services here."""

    settings: Settings
    ready_checks: dict[str, ReadyCheck] = field(default_factory=dict)

    async def readiness_failures(self) -> dict[str, str]:
        """Run registered readiness checks; engine with no checks is ready."""
        failures: dict[str, str] = {}
        for name, check in self.ready_checks.items():
            reason = await check()
            if reason is not None:
                failures[name] = reason
        return failures


def create_container(settings: Settings) -> Container:
    return Container(settings=settings)
