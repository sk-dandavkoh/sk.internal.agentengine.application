"""Database engine/session lifecycle for PostgreSQL (async SQLAlchemy).

The engine is owned by the composition root and injected into repositories.
``pool_pre_ping`` recycles dead connections transparently; connections are
never held across idle request boundaries. Session/transaction ownership
belongs to the Unit of Work — this module only manages the engine.
"""

from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

if TYPE_CHECKING:
    from alembic.config import Config


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

    async def schema_revision(self) -> str | None:
        """The applied Alembic revision; None before the first migration."""
        from sqlalchemy import inspect, text

        async with self._engine.connect() as conn:
            exists = await conn.run_sync(
                lambda sync: inspect(sync).has_table("alembic_version")
            )
            if not exists:
                return None
            revision: str | None = await conn.scalar(
                text("SELECT version_num FROM alembic_version")
            )
            return revision

    async def check_schema(self) -> str | None:
        """Readiness probe: None when the schema is at this engine's head
        (a new engine on an unmigrated database must not serve)."""
        try:
            revision = await self.schema_revision()
        except Exception as exc:
            return f"database unreachable: {type(exc).__name__}"
        head = _alembic_head()
        if revision != head:
            return f"schema at {revision}, engine needs {head}: run akasha migrate"
        return None

    async def run_migrations(self, url: str) -> None:
        """Apply Alembic migrations against this database.

        Alembic's core is synchronous; it runs on a worker thread so the
        async engine's event loop is never blocked.
        """
        import asyncio

        from alembic import command

        await asyncio.to_thread(command.upgrade, _alembic_config(url), "head")

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


def _alembic_config(url: str | None = None) -> "Config":
    from alembic.config import Config

    # backend/src/akasha/infrastructure/database.py -> backend/ (the image
    # installs the project editable, so this holds there too).
    package_root = Path(__file__).resolve().parents[3]
    cfg = Config(str(package_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(package_root / "migrations"))
    if url is not None:
        cfg.set_main_option("sqlalchemy.url", normalize_sync_url(url))
    return cfg


@cache
def _alembic_head() -> str | None:
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(_alembic_config()).get_current_head()
