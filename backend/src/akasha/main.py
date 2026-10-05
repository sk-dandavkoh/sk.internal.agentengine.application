"""Process entry point: load config, build the app, serve it with uvicorn."""

import uvicorn
from fastapi import FastAPI

from akasha.api.app import create_app
from akasha.config import Settings, load_settings
from akasha.observability.logging import configure_logging


def create_served_app() -> FastAPI:
    """The app as served: this process owns logging, in every environment
    (AKASHA_LOG_LEVEL also decides which node log lines are stored).
    Called by uvicorn after it set up its own loggers, so the root logger
    configured here is the final one."""
    settings = load_settings()
    configure_logging(settings.log_level)
    return create_app(settings)


def main() -> int:
    settings: Settings = load_settings()  # fails fast on invalid config
    uvicorn.run(
        "akasha.main:create_served_app",
        factory=True,
        host=settings.host,
        port=settings.port,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
