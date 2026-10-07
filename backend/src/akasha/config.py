"""Central configuration.

All environment access happens here — never via scattered ``os.getenv`` calls.
Missing or invalid critical configuration fails fast at startup (see
``Settings.model_validate`` in the bootstrap path).
"""

from datetime import timedelta
from pathlib import Path

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    DirectoryPath,
    Field,
    SecretStr,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from akasha.domain.limits import ResourceLimits

# Docker Compose's `secrets` service generates the database URL and the
# credential key into this volume, one file per setting named like its
# variable (docs/deployment.md). Environment variables take precedence.
# Only used where it exists: elsewhere pydantic-settings would warn.
SECRETS_DIR = Path("/run/akasha-secrets")

PROVIDER_NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,31}$"


class OpenAICompatibleEndpoint(BaseModel):
    """One OpenAI-compatible LLM endpoint (vLLM, OpenRouter, LM Studio, ...)."""

    name: str = Field(pattern=PROVIDER_NAME_PATTERN)
    base_url: AnyHttpUrl
    # Keyless servers (a local vLLM) set this to false.
    requires_credential: bool = True


class Settings(BaseSettings):
    """Engine configuration, loaded from environment variables.

    Example: host is set via ``AKASHA_HOST=0.0.0.0``.
    """

    model_config = SettingsConfigDict(
        env_prefix="AKASHA_",
        secrets_dir=SECRETS_DIR if SECRETS_DIR.is_dir() else None,
    )

    environment: str = "development"
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"
    database_url: str | None = None  # required when persistence is enabled

    # Outbound network policy for user-configured destinations (core.http,
    # core.smtp). Private/loopback/link-local targets are blocked unless
    # allowed here: globally, or per host (JSON list, e.g. '["intranet"]').
    network_allow_private: bool = False
    network_allowed_hosts: list[str] = Field(default_factory=list)
    http_max_response_bytes: int = Field(default=5 * 1024 * 1024, gt=0)
    # SearXNG base URL for core.web_search; unset → the node fails with
    # SEARCH_NOT_CONFIGURED. Operator-trusted, so it may be private.
    web_search_url: AnyHttpUrl | None = None

    # Agent tools (ADR 0005). File nodes and core.command work inside one
    # directory per workflow below this existing directory; unset → they
    # fail with WORKSPACE_NOT_CONFIGURED. Engine data never belongs here.
    workspace_dir: DirectoryPath | None = None
    workspace_max_file_bytes: int = Field(default=1024 * 1024, gt=0)
    # Programs core.command may run (JSON list of names on PATH or absolute
    # paths). Empty → the node is disabled. Not a sandbox: an allowed
    # interpreter (python3, bash) lets workflows run arbitrary code as the
    # engine's OS user.
    command_allowed: list[str] = Field(default_factory=list)
    command_max_output_bytes: int = Field(default=256 * 1024, gt=0)

    # Workers + queue (Phase 22). Unset → no queue: executions created
    # through the API stay QUEUED (nothing runs them). The URL may carry a
    # password. Every installation sharing a Redis needs its own prefix.
    redis_url: SecretStr | None = None
    queue_prefix: str = Field(default="akasha", pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$")
    # Run a worker inside the API process; false when dedicated
    # ``akasha worker`` processes take the jobs.
    worker_enabled: bool = True
    worker_concurrency: int = Field(default=4, ge=1, le=256)
    worker_poll_seconds: float = Field(default=1.0, gt=0)
    worker_heartbeat_seconds: float = Field(default=5.0, gt=0)
    # On stop, runs get this long to finish before they are cancelled.
    worker_shutdown_grace_seconds: float = Field(default=30.0, ge=0)
    # How often lost jobs are recovered, and how long an execution may stay
    # QUEUED before it is dispatched again.
    queue_recovery_seconds: float = Field(default=30.0, gt=0)
    # A started execution whose owner has not heart-beaten (every
    # worker_heartbeat_seconds) for this long is recovered. Keep it several
    # heartbeats long: a live but slow owner past it loses its run.
    execution_stale_seconds: float = Field(default=60.0, gt=0)

    # Trigger system (Phase 10). CRON/INTERVAL triggers are fired by one
    # in-process scheduler task per API process; EVENT triggers by a
    # dispatcher task next to it. Both run only where this is true.
    scheduler_enabled: bool = True
    scheduler_poll_seconds: float = Field(default=1.0, gt=0)
    # Live execution WebSocket (§23): how often a subscription reads new
    # events from the log and re-checks its session.
    live_poll_seconds: float = Field(default=0.5, gt=0)
    # Cap on every API request body, streamed or declared (Phase 25). Keep
    # it above the 16 MiB workflow package limit, or imports fail first.
    api_max_request_bytes: int = Field(default=20 * 1024 * 1024, gt=0)
    # Inbound webhook body cap; the payload becomes the execution input.
    webhook_max_payload_bytes: int = Field(default=1024 * 1024, gt=0)

    # Credential Store (Phase 13): 32 random bytes, URL-safe base64 (see
    # ``akasha.security.crypto``). Unset → credentials are unavailable.
    # Never commit it; losing it makes stored credentials unrecoverable.
    credential_encryption_key: SecretStr | None = None

    # AI providers (Phase 14). OpenAI and Anthropic are always configured and
    # need a credential reference per request; Ollama only when its URL is
    # set. The timeout is the ceiling a request may shorten, never extend.
    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    ollama_base_url: AnyHttpUrl | None = None
    # One generic OpenAI-compatible endpoint (vLLM, OpenRouter, LM Studio,
    # ...), registered under ``openai_compatible_name`` when the URL is set.
    # Keyless servers (a local vLLM) set requires_credential to false.
    openai_compatible_base_url: AnyHttpUrl | None = None
    openai_compatible_name: str = Field(
        default="openai_compatible", pattern=PROVIDER_NAME_PATTERN
    )
    openai_compatible_requires_credential: bool = True
    # Any number of further OpenAI-compatible endpoints, as a JSON list:
    # '[{"name": "openrouter", "base_url": "https://openrouter.ai/api/v1"},
    #   {"name": "vllm", "base_url": "http://vllm:8000/v1",
    #    "requires_credential": false}]'
    llm_endpoints: list[OpenAICompatibleEndpoint] = Field(default_factory=list)

    # Authentication (Phase 20). Sessions expire this long after login;
    # there is no sliding renewal.
    session_ttl_seconds: int = Field(default=12 * 60 * 60, ge=60, le=30 * 24 * 60 * 60)
    # The browser session cookie (ADR 0002) is sent only over HTTPS. Turn off
    # only for plain-HTTP setups on a non-localhost address (e.g. a test rig).
    session_cookie_secure: bool = True
    # Static bearer token for Prometheus to scrape /metrics. Unset → only an
    # admin session may read /metrics. Never commit it.
    metrics_token: SecretStr | None = Field(default=None, min_length=32)

    # Plugin System (Phase 17): directory of installed plugin packages; it
    # must exist. Unset → no plugins. Plugins are trusted code: only
    # operators may write here.
    plugins_dir: Path | None = None
    # Accept plugin package uploads (``POST /api/v1/plugins``, ADR 0001).
    # Off by default: an upload is code the engine runs, so turning it on
    # makes every admin account as powerful as shell access to the host.
    # Enable/disable/remove over HTTP need no opt-in (no new code).
    plugins_http_install: bool = False

    # MCP control plane (docs/mcp.md, ADR 0006). Off: no /mcp route exists
    # at all. On: Streamable HTTP at /mcp of this API process, bearer
    # session tokens only, the same authorization as the REST API.
    # Startup-only: changing any of these needs a restart.
    mcp_enabled: bool = False
    # Cap on one MCP request body (plugin sources and workflow packages
    # travel inline, base64 for binary).
    mcp_max_request_bytes: int = Field(default=4 * 1024 * 1024, gt=0)
    # Cap on one tool result (JSON bytes): agents have context limits, so
    # an oversized result is an error that asks for a smaller page.
    mcp_max_response_bytes: int = Field(default=256 * 1024, ge=1024)
    # Let MCP admins hand the engine new code: install, update and test
    # plugin packages. Off by default: like AKASHA_PLUGINS_HTTP_INSTALL it
    # makes every admin session (and the agent holding it) as powerful as
    # shell access to the host. Enable/disable/remove need no opt-in.
    mcp_plugin_code: bool = False

    # Resource limits (Phase 24, ``akasha.domain.limits``). Execution and
    # node timeouts are both the default and the ceiling a request may lower.
    # Attempts include the first one, so 5 allows 4 retries.
    limit_execution_timeout_seconds: float = Field(default=3600.0, gt=0)
    limit_node_timeout_seconds: float = Field(default=900.0, gt=0)
    limit_node_attempts: int = Field(default=5, ge=1, le=100)
    limit_graph_nodes: int = Field(default=500, ge=1, le=100_000)
    limit_graph_depth: int = Field(default=100, ge=1, le=100_000)
    limit_active_executions_per_workflow: int = Field(default=100, ge=1)
    limit_node_output_bytes: int = Field(default=1024 * 1024, gt=0)
    limit_execution_output_bytes: int = Field(default=16 * 1024 * 1024, gt=0)
    # Nodes in flight per execution; executions per worker: worker_concurrency.
    limit_execution_concurrency: int = Field(default=8, ge=1, le=256)
    # Per LLM request; requests without max_tokens get this many.
    limit_llm_output_tokens: int = Field(default=16_384, ge=1, le=1_000_000)
    # Longest chain of event-triggered executions (architecture §130/§131):
    # a run started by an event trigger is one deeper than its source.
    limit_event_trigger_depth: int = Field(default=8, ge=1, le=100)

    @model_validator(mode="after")
    def _stale_after_heartbeats(self) -> "Settings":
        if self.execution_stale_seconds < 3 * self.worker_heartbeat_seconds:
            raise ValueError(
                "execution_stale_seconds must be at least 3 worker heartbeats"
            )
        self.resource_limits()  # fail fast on contradictory limits
        return self

    def resource_limits(self) -> ResourceLimits:
        return ResourceLimits(
            execution_timeout=timedelta(seconds=self.limit_execution_timeout_seconds),
            node_timeout=timedelta(seconds=self.limit_node_timeout_seconds),
            max_node_attempts=self.limit_node_attempts,
            max_graph_nodes=self.limit_graph_nodes,
            max_graph_depth=self.limit_graph_depth,
            max_active_executions_per_workflow=self.limit_active_executions_per_workflow,
            max_node_output_bytes=self.limit_node_output_bytes,
            max_execution_output_bytes=self.limit_execution_output_bytes,
        )


def load_settings() -> Settings:
    """Load and validate configuration. Raises ValidationError on bad config."""
    return Settings()
