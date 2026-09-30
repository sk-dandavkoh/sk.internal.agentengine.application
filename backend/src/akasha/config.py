"""Central configuration.

All environment access happens here — never via scattered ``os.getenv`` calls.
Missing or invalid critical configuration fails fast at startup (see
``Settings.model_validate`` in the bootstrap path).
"""

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

    # Trigger system (Phase 10). Until workers exist (Phase 22) CRON/INTERVAL
    # triggers are fired by one in-process scheduler task per API process.
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


def load_settings() -> Settings:
    """Load and validate configuration. Raises ValidationError on bad config."""
    return Settings()
