"""Application composition: single, explicit build point for dependencies.

The Container is created once at startup and stored on ``app.state``. No
global singletons — tests build their own Container and FastAPI app via
``create_container`` / ``create_app``.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from akasha.application.ai import AIService, LLMProvider
from akasha.application.credentials import CredentialService
from akasha.application.events import EventPublisher
from akasha.application.executions import ExecutionService
from akasha.application.projects import ProjectService
from akasha.application.triggers import TriggerRegistry, TriggerService
from akasha.config import Settings
from akasha.domain.ai import LLMClient
from akasha.infrastructure.database import Database, normalize_async_url
from akasha.infrastructure.persistence import SqlAlchemyUnitOfWork
from akasha.nodes.builtins import create_builtin_registry
from akasha.nodes.registry import NodeRegistry
from akasha.observability.metrics import EngineMetrics
from akasha.providers.litellm_adapter import LiteLLMAdapter
from akasha.runtime.scheduler import TriggerScheduler
from akasha.security.crypto import SecretCipher
from akasha.security.network import NetworkPolicy

# A readiness check returns None when healthy, or a human-readable reason.
ReadyCheck = Callable[[], Awaitable[str | None]]


@dataclass
class Container:
    """Composition root. Application services get a UnitOfWork factory —
    one UnitOfWork per use case / transaction boundary."""

    settings: Settings
    nodes: NodeRegistry = field(default_factory=NodeRegistry)
    # One in-process fan-out shared by every producer; consumers (WebSocket
    # gateway, metrics) subscribe here.
    events: EventPublisher = field(default_factory=EventPublisher)
    # Served at /metrics; the runtime must share it once the container
    # constructs one (Phase 21/22), like ``events``.
    metrics: EngineMetrics = field(default_factory=EngineMetrics)
    database: Database | None = None
    projects: ProjectService | None = None
    executions: ExecutionService | None = None
    triggers: TriggerService | None = None
    # Needs the database and AKASHA_CREDENTIAL_ENCRYPTION_KEY. The runtime
    # must receive this same service (Phase 21/22 wiring) to resolve secrets.
    credentials: CredentialService | None = None
    trigger_registry: TriggerRegistry | None = None
    # core.llm calls providers through this service only (it is the
    # registry's LLMClient).
    ai: AIService | None = None
    scheduler: TriggerScheduler | None = None
    ready_checks: dict[str, ReadyCheck] = field(default_factory=dict)
    # The scheduler loop task; the container owns its lifecycle (§44/§122).
    _scheduler_task: asyncio.Task[None] | None = field(
        default=None, init=False, repr=False
    )

    async def start(self) -> None:
        """Start owned background work (the in-process trigger scheduler)."""
        if self.scheduler is not None and self._scheduler_task is None:
            self._scheduler_task = asyncio.create_task(
                self.scheduler.run(), name="trigger-scheduler"
            )

    async def readiness_failures(self) -> dict[str, str]:
        """Run registered readiness checks; engine with no checks is ready."""
        failures: dict[str, str] = {}
        for name, check in self.ready_checks.items():
            reason = await check()
            if reason is not None:
                failures[name] = reason
        return failures

    async def shutdown(self) -> None:
        """Stop background work, then release connections and pools (§48)."""
        task = self._scheduler_task
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            self._scheduler_task = None
        if self.database is not None:
            await self.database.dispose()


def create_container(settings: Settings) -> Container:
    """Wire infrastructure from configuration.

    A missing ``database_url`` yields a container *without* a database or
    trigger services: the engine still boots, project/trigger endpoints answer
    ``CONFIGURATION_ERROR``, and readiness has nothing to check. Tests inject
    their own container.
    """
    # Built first: a malformed key fails startup even without a database.
    key = settings.credential_encryption_key
    cipher = None if key is None else SecretCipher.from_config(key.get_secret_value())
    ai = create_ai_service(settings)
    nodes = create_node_registry(settings, llm=ai)
    if not settings.database_url:
        return Container(settings=settings, nodes=nodes, ai=ai)
    database = Database(normalize_async_url(settings.database_url))

    def uow() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(database)

    # The same registry must later back the ExecutionRuntime (Phase 21/22
    # wiring), so publish-time and run-time validation agree.
    events = EventPublisher()
    metrics = EngineMetrics()
    projects = ProjectService(uow, nodes.definitions, events)
    executions = ExecutionService(uow, events, metrics)
    triggers = TriggerService(executions)
    trigger_registry = TriggerRegistry(uow)
    scheduler = (
        TriggerScheduler(
            trigger_registry, triggers, poll_seconds=settings.scheduler_poll_seconds
        )
        if settings.scheduler_enabled
        else None
    )
    return Container(
        settings=settings,
        nodes=nodes,
        events=events,
        metrics=metrics,
        database=database,
        projects=projects,
        executions=executions,
        triggers=triggers,
        credentials=None if cipher is None else CredentialService(uow, cipher),
        trigger_registry=trigger_registry,
        ai=ai,
        scheduler=scheduler,
        ready_checks={"database": database.check},
    )


def create_node_registry(
    settings: Settings, *, llm: LLMClient | None = None
) -> NodeRegistry:
    """Built-in nodes configured from settings (plugins join in Phase 17)."""
    return create_builtin_registry(
        llm=llm,
        network=NetworkPolicy(
            allow_private_networks=settings.network_allow_private,
            allowed_hosts=frozenset(settings.network_allowed_hosts),
        ),
        web_search_url=None
        if settings.web_search_url is None
        else str(settings.web_search_url),
        http_max_response_bytes=settings.http_max_response_bytes,
    )


def create_ai_service(settings: Settings) -> AIService:
    timeout = settings.llm_timeout_seconds
    providers = [
        LLMProvider("openai", LiteLLMAdapter("openai"), timeout_seconds=timeout),
        LLMProvider("anthropic", LiteLLMAdapter("anthropic"), timeout_seconds=timeout),
    ]
    if settings.ollama_base_url is not None:
        providers.append(
            LLMProvider(
                "ollama",
                # ollama_chat (not ollama) speaks the chat API, incl. tools.
                LiteLLMAdapter("ollama_chat", api_base=str(settings.ollama_base_url)),
                requires_credential=False,
                timeout_seconds=timeout,
            )
        )
    if settings.openai_compatible_base_url is not None:
        providers.append(
            LLMProvider(
                settings.openai_compatible_name,
                LiteLLMAdapter(
                    "openai", api_base=str(settings.openai_compatible_base_url)
                ),
                requires_credential=settings.openai_compatible_requires_credential,
                timeout_seconds=timeout,
            )
        )
    # A name clashing with a built-in provider fails startup (ConfigurationError).
    return AIService(providers)
