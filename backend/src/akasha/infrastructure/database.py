"""Database engine/session lifecycle for PostgreSQL (async SQLAlchemy).

The engine is owned by the composition root and injected into repositories.
``pool_pre_ping`` recycles dead connections transparently; connections are
never held across idle request boundaries. Session/transaction ownership
belongs to the Unit of Work — this module only manages the engine.
"""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class Database:
    """Owns the engine; hands out sessions with explicit boundaries."""

    def __init__(self, url: str, *, echo: bool = False) -> None:
        self._engine: AsyncEngine = create_async_engine(
            url,
            echo=echo,
            pool_pre_ping=True,  # transparently revive dropped connections
            pool_size=5,
            max_overflow=5,
        )
        self._sessions = async_sessionmaker(self._engine, expire_on_commit=False)

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    def session(self) -> AsyncSession:
        return self._sessions()

    async def check(self) -> str | None:
        """Readiness probe: None when a trivial round-trip succeeds."""
        from sqlalchemy import text

        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:
            return f"database unreachable: {type(exc).__name__}"
        return None

    async def run_migrations(self, url: str) -> None:
        """Apply Alembic migrations against this database.

        Alembic's core is synchronous; it runs on a worker thread so the
        async engine's event loop is never blocked.
        """
        import asyncio

        from alembic import command
        from alembic.config import Config

        def _upgrade() -> None:
            from pathlib import Path

            # backend/src/akasha/infrastructure/database.py -> backend/
            package_root = Path(__file__).resolve().parents[3]
            cfg = Config(str(package_root / "alembic.ini"))
            cfg.set_main_option("script_location", str(package_root / "migrations"))
            cfg.set_main_option("sqlalchemy.url", normalize_sync_url(url))
            command.upgrade(cfg, "head")

        await asyncio.to_thread(_upgrade)

    async def dispose(self) -> None:
        await self._engine.dispose()


def normalize_async_url(url: str) -> str:
    """Rewrite a plain postgres URL to the asyncpg dialect.

    Accepts both bare ``postgresql://`` and explicit dialect prefixes so
    config files don't need to know the driver name.
    """
    if "+asyncpg" in url:
        return url
    scheme, _, rest = url.partition("://")
    if scheme in ("postgresql", "postgres"):
        return f"postgresql+asyncpg://{rest}"
    return url


def normalize_sync_url(url: str) -> str:
    """Alembic runs synchronously; map the URL to the psycopg3 driver."""
    url = url.replace("+asyncpg", "")
    scheme, sep, rest = url.partition("://")
    if sep and "+" not in scheme and scheme in ("postgresql", "postgres"):
        return f"postgresql+psycopg://{rest}"
    return url
