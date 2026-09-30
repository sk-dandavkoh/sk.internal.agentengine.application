"""Central configuration.

All environment access happens here — never via scattered ``os.getenv`` calls.
Missing or invalid critical configuration fails fast at startup (see
``Settings.model_validate`` in the bootstrap path).
"""

from pathlib import Path

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Engine configuration, loaded from environment variables.

    Example: host is set via ``AKASHA_HOST=0.0.0.0``.
    """

    model_config = SettingsConfigDict(env_prefix="AKASHA_")

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

    # Trigger system (Phase 10). CRON/INTERVAL triggers are fired by one
    # in-process scheduler task per API process.
    scheduler_enabled: bool = True
    scheduler_poll_seconds: float = Field(default=1.0, gt=0)
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
        default="openai_compatible", pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$"
    )
    openai_compatible_requires_credential: bool = True

    # Authentication (Phase 20). Sessions expire this long after login;
    # there is no sliding renewal.
    session_ttl_seconds: int = Field(default=12 * 60 * 60, ge=60, le=30 * 24 * 60 * 60)
    # Static bearer token for Prometheus to scrape /metrics. Unset → only an
    # admin session may read /metrics. Never commit it.
    metrics_token: SecretStr | None = Field(default=None, min_length=32)

    # Plugin System (Phase 17): directory of installed plugin packages; it
    # must exist. Unset → no plugins. Plugins are trusted code: only
    # operators may write here.
    plugins_dir: Path | None = None


def load_settings() -> Settings:
    """Load and validate configuration. Raises ValidationError on bad config."""
    return Settings()
