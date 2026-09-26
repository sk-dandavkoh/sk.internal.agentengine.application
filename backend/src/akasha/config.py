"""Central configuration.

All environment access happens here — never via scattered ``os.getenv`` calls.
Missing or invalid critical configuration fails fast at startup (see
``Settings.model_validate`` in the bootstrap path).
"""

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


def load_settings() -> Settings:
    """Load and validate configuration. Raises ValidationError on bad config."""
    return Settings()
