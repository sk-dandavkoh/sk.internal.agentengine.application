"""Process entry point: load config, build the app, serve it with uvicorn."""

import uvicorn

from akasha.config import Settings, load_settings


def main() -> int:
    settings: Settings = load_settings()  # fails fast on invalid config
    uvicorn.run(
        "akasha.api.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
