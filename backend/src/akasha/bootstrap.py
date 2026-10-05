"""Application composition: single, explicit build point for dependencies.

The Container is created once at startup and stored on ``app.state``. No
global singletons — tests build their own Container and FastAPI app via
``create_container`` / ``create_app``.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from types import MappingProxyType

from akasha.application.ai import AIService, LLMProvider
from akasha.application.auth import (
    AuthenticationService,
    AuthorizationService,
    UserService,
)
from akasha.application.credentials import CredentialService
from akasha.application.events import EventPublisher
from akasha.application.executions import ExecutionService
from akasha.application.monitoring import MonitoringService
from akasha.application.packages import ProjectPackageService
from akasha.application.projects import ProjectService
from akasha.application.reports import ReportService
from akasha.application.triggers import TriggerRegistry, TriggerService
from akasha.config import Settings
from akasha.domain.ai import LLMClient
from akasha.infrastructure.database import Database, normalize_async_url
from akasha.infrastructure.persistence import SqlAlchemyUnitOfWork
from akasha.infrastructure.redis_queue import RedisExecutionQueue
from akasha.nodes.builtins import create_builtin_registry
from akasha.nodes.registry import NodeRegistry
from akasha.observability.metrics import EngineMetrics
from akasha.plugins.registry import PluginRegistry
from akasha.providers.litellm_adapter import LiteLLMAdapter
from akasha.runtime.event_triggers import EventTriggerDispatcher
from akasha.runtime.runtime import ExecutionRuntime
from akasha.runtime.scheduler import TriggerScheduler
from akasha.security.crypto import SecretCipher
from akasha.security.network import NetworkPolicy
from akasha.security.paths import Workspace
from akasha.workers import Worker, WorkerManager

# A readiness check returns None when healthy, or a human-readable reason.
ReadyCheck = Callable[[], Awaitable[str | None]]


@dataclass
class Container:
    """Composition root. Application services get a UnitOfWork factory —
    one UnitOfWork per use case / transaction boundary."""

    settings: Settings
    nodes: NodeRegistry = field(default_factory=NodeRegistry)
    # One in-process fan-out shared by every producer (at-most-once, this
    # process only). The live WebSocket (api/live.py) deliberately reads the
    # persisted event log instead: workers run in other processes.
    events: EventPublisher = field(default_factory=EventPublisher)
    # Served at /metrics; shared with the runtime, like ``events``.
    metrics: EngineMetrics = field(default_factory=EngineMetrics)
    # Installed plugins; their nodes live in ``nodes`` like any other.
    plugins: PluginRegistry | None = None
    database: Database | None = None
    projects: ProjectService | None = None
    packages: ProjectPackageService | None = None
    executions: ExecutionService | None = None
    # The runtime workers hand jobs to; the CLI's ``project run`` uses it
    # directly.
    runtime: ExecutionRuntime | None = None
    # Needs the database and AKASHA_REDIS_URL (Phase 22).
    queue: RedisExecutionQueue | None = None
    workers: WorkerManager | None = None
    reports: ReportService | None = None
    monitoring: MonitoringService | None = None
    triggers: TriggerService | None = None
    # Needs the database and AKASHA_CREDENTIAL_ENCRYPTION_KEY. The runtime
    # resolves secrets through this same service.
    credentials: CredentialService | None = None
    trigger_registry: TriggerRegistry | None = None
    # core.llm calls providers through this service only (it is the
    # registry's LLMClient).
    ai: AIService | None = None
    scheduler: TriggerScheduler | None = None
    # Fires EVENT triggers from the event log; runs where the scheduler runs.
    event_triggers: EventTriggerDispatcher | None = None
    # Authentication (Phase 20). Without them every protected route answers
    # CONFIGURATION_ERROR: the API fails closed, never open.
    auth: AuthenticationService | None = None
    authorization: AuthorizationService | None = None
    users: UserService | None = None
    ready_checks: dict[str, ReadyCheck] = field(default_factory=dict)
    # The scheduler and event-trigger loop tasks; the container owns their
    # lifecycle (§44/§122).
    _tasks: list[asyncio.Task[None]] = field(
        default_factory=list, init=False, repr=False
    )

    async def start(self) -> None:
        """Start owned background work: the in-process trigger scheduler and,
        unless dedicated worker processes take the jobs, a worker."""
        if self.workers is not None and self.settings.worker_enabled:
            await self.workers.start()
        if self._tasks:
            return
        if self.scheduler is not None:
            self._tasks.append(
                asyncio.create_task(self.scheduler.run(), name="trigger-scheduler")
            )
        if self.event_triggers is not None:
            self._tasks.append(
                asyncio.create_task(
                    self.event_triggers.run(), name="event-trigger-dispatcher"
                )
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
        for task in self._tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        # Before the database closes: stopping runs persist their state.
        if self.workers is not None:
            await self.workers.stop()
        if self.queue is not None:
            await self.queue.close()
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
    plugins = None
    if settings.plugins_dir is not None:
        plugins = PluginRegistry(settings.plugins_dir, nodes)
        plugins.load_installed()
    if not settings.database_url:
        return Container(settings=settings, nodes=nodes, plugins=plugins, ai=ai)
    database = Database(normalize_async_url(settings.database_url))

    def uow() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(database)

    # The same registry backs the ExecutionRuntime, so publish-time and
    # run-time validation agree.
    events = EventPublisher()
    metrics = EngineMetrics()
    plugin_versions = MappingProxyType({}) if plugins is None else plugins.versions
    projects = ProjectService(uow, nodes.definitions, events, plugins=plugin_versions)
    packages = ProjectPackageService(
        uow, nodes.definitions, events, plugins=plugin_versions
    )
    redis_url = settings.redis_url
    queue = (
        None
        if redis_url is None
        else RedisExecutionQueue.from_url(
            redis_url.get_secret_value(), prefix=settings.queue_prefix
        )
    )
    limits = settings.resource_limits()
    executions = ExecutionService(uow, events, metrics, queue=queue, limits=limits)
    credentials = None if cipher is None else CredentialService(uow, cipher)
    runtime = ExecutionRuntime(
        uow,
        nodes.catalog,
        events=events,
        metrics=metrics,
        credentials=credentials,
        heartbeat_seconds=settings.worker_heartbeat_seconds,
        max_concurrency=settings.limit_execution_concurrency,
        limits=limits,
    )
    workers = (
        None
        if queue is None
        else WorkerManager(
            Worker(
                queue,
                runtime,
                concurrency=settings.worker_concurrency,
                poll_seconds=settings.worker_poll_seconds,
                heartbeat_seconds=settings.worker_heartbeat_seconds,
                shutdown_grace_seconds=settings.worker_shutdown_grace_seconds,
            ),
            queue,
            executions,
            metrics=metrics,
            recovery_seconds=settings.queue_recovery_seconds,
            stale_seconds=settings.execution_stale_seconds,
        )
    )
    ready_checks: dict[str, ReadyCheck] = {
        "database": database.check,
        "schema": database.check_schema,
    }
    if queue is not None:
        ready_checks["queue"] = queue.ping
    auth = AuthenticationService(
        uow, session_ttl=timedelta(seconds=settings.session_ttl_seconds)
    )
    triggers = TriggerService(executions)
    trigger_registry = TriggerRegistry(uow)
    scheduler = (
        TriggerScheduler(
            trigger_registry, triggers, poll_seconds=settings.scheduler_poll_seconds
        )
        if settings.scheduler_enabled
        else None
    )
    event_triggers = (
        EventTriggerDispatcher(
            uow,
            trigger_registry,
            triggers,
            max_depth=settings.limit_event_trigger_depth,
            poll_seconds=settings.scheduler_poll_seconds,
        )
        if settings.scheduler_enabled
        else None
    )
    return Container(
        settings=settings,
        nodes=nodes,
        plugins=plugins,
        events=events,
        metrics=metrics,
        database=database,
        projects=projects,
        packages=packages,
        executions=executions,
        runtime=runtime,
        queue=queue,
        workers=workers,
        reports=ReportService(uow),
        monitoring=MonitoringService(uow),
        triggers=triggers,
        credentials=credentials,
        trigger_registry=trigger_registry,
        ai=ai,
        scheduler=scheduler,
        event_triggers=event_triggers,
        auth=auth,
        authorization=AuthorizationService(uow),
        users=UserService(uow, auth),
        ready_checks=ready_checks,
    )


def create_node_registry(
    settings: Settings, *, llm: LLMClient | None = None
) -> NodeRegistry:
    """Built-in nodes configured from settings; plugins add theirs."""
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
        workspace=None
        if settings.workspace_dir is None
        else Workspace(settings.workspace_dir),
        max_file_bytes=settings.workspace_max_file_bytes,
        allowed_commands=frozenset(settings.command_allowed),
        command_max_output_bytes=settings.command_max_output_bytes,
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
    return AIService(providers, max_output_tokens=settings.limit_llm_output_tokens)
