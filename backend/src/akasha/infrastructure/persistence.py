"""SQLAlchemy implementations of the application-layer persistence interfaces.

Domain objects are rebuilt from rows (and JSONB documents) explicitly — no
ORM identity map, no lazy loading across the boundary (architecture §79).
Repositories see an externally owned ``AsyncSession``; commits and rollbacks
are the exclusive job of ``SqlAlchemyUnitOfWork`` (backend/AGENTS.md §16).
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, cast

from sqlalchemy import Insert, Update, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult, RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from akasha.application.persistence import (
    ConcurrentModificationError,
    ProjectRepository,
    ProjectVersionRepository,
    UnitOfWork,
)
from akasha.domain.primitives import Timestamps
from akasha.domain.project import (
    Project,
    ProjectDraft,
    ProjectStatus,
    ProjectVersion,
)
from akasha.infrastructure import db_models as m
from akasha.infrastructure.database import Database
from akasha.infrastructure.graph_serialization import (
    deserialize_dependency,
    deserialize_graph,
    deserialize_trigger,
    serialize_dependency,
    serialize_graph,
    serialize_trigger,
)


def _ensure_utc(dt: datetime) -> datetime:
    """asyncpg returns naive datetimes for timestamptz; domain expects UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


class SqlAlchemyProjectRepository(ProjectRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, project_id: str) -> Project | None:
        row = (
            (
                await self._session.execute(
                    select(m.projects).where(m.projects.c.id == project_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return self._to_domain(row)

    async def save(self, project: Project) -> None:
        values = {
            "name": project.name,
            "description": project.draft.description,
            "status": project.status.value,
            "draft_graph": serialize_graph(project.draft.graph),
            "draft_triggers": [serialize_trigger(t) for t in project.draft.triggers],
            "draft_dependencies": [
                serialize_dependency(d) for d in project.draft.dependencies
            ],
            "current_version": project.current_version,
            "revision": project.revision + 1,
            "created_at": project.timestamps.created_at,
            "updated_at": project.timestamps.updated_at,
        }
        # Single-statement CAS: PostgreSQL re-checks the WHERE clause after
        # waiting for a concurrent writer's row lock, so two writers holding
        # the same revision can never both succeed (no lost updates).
        if project.revision == 0:
            stmt: Insert | Update = (
                pg_insert(m.projects)
                .values(id=project.id, **values)
                .on_conflict_do_nothing(index_elements=[m.projects.c.id])
            )
        else:
            stmt = (
                m.projects.update()
                .where(
                    m.projects.c.id == project.id,
                    m.projects.c.revision == project.revision,
                )
                .values(**values)
            )
        result = cast("CursorResult[Any]", await self._session.execute(stmt))
        if result.rowcount != 1:
            raise ConcurrentModificationError(
                "The project was modified concurrently; reload and retry.",
                details={"project_id": project.id},
            )
        project.revision += 1

    async def delete(self, project_id: str, *, revision: int) -> bool:
        result = cast(
            "CursorResult[Any]",
            await self._session.execute(
                delete(m.projects).where(
                    m.projects.c.id == project_id, m.projects.c.revision == revision
                )
            ),
        )
        return result.rowcount > 0

    async def list(self, *, offset: int = 0, limit: int = 100) -> Sequence[Project]:
        rows = (
            (
                await self._session.execute(
                    select(m.projects)
                    .order_by(m.projects.c.created_at, m.projects.c.id)
                    .offset(offset)
                    .limit(limit)
                )
            )
            .mappings()
            .all()
        )
        return [self._to_domain(row) for row in rows]

    @staticmethod
    def _to_domain(row: RowMapping) -> Project:
        r = dict(row)
        draft = ProjectDraft(
            graph=deserialize_graph(dict(r["draft_graph"])),
            triggers=[deserialize_trigger(t) for t in r["draft_triggers"]],
            dependencies=[deserialize_dependency(d) for d in r["draft_dependencies"]],
            description=r["description"],
        )
        return Project(
            id=r["id"],
            name=r["name"],
            draft=draft,
            status=ProjectStatus(r["status"]),
            current_version=r["current_version"],
            revision=r["revision"],
            timestamps=Timestamps(
                created_at=_ensure_utc(r["created_at"]),
                updated_at=_ensure_utc(r["updated_at"]),
            ),
        )


class SqlAlchemyProjectVersionRepository(ProjectVersionRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, project_id: str, version: int) -> ProjectVersion | None:
        row = (
            (
                await self._session.execute(
                    select(m.project_versions).where(
                        m.project_versions.c.project_id == project_id,
                        m.project_versions.c.version == version,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return self._to_domain(row)

    async def save(self, project_id: str, version: ProjectVersion) -> None:
        # Insert-only by contract: a second save of the same (project_id,
        # version) violates the unique constraint — the database is the last
        # line of defence for immutability (architecture §3.7).
        await self._session.execute(
            m.project_versions.insert().values(
                id=version.id,
                project_id=project_id,
                version=version.version,
                schema_version=version.schema_version,
                graph=serialize_graph(version.graph),
                triggers=[serialize_trigger(t) for t in version.triggers],
                dependencies=[serialize_dependency(d) for d in version.dependencies],
                description=version.description,
                published_at=version.published_at,
            )
        )

    async def list(
        self, project_id: str, *, offset: int = 0, limit: int = 100
    ) -> Sequence[ProjectVersion]:
        rows = (
            (
                await self._session.execute(
                    select(m.project_versions)
                    .where(m.project_versions.c.project_id == project_id)
                    .order_by(m.project_versions.c.version)
                    .offset(offset)
                    .limit(limit)
                )
            )
            .mappings()
            .all()
        )
        return [self._to_domain(row) for row in rows]

    @staticmethod
    def _to_domain(row: RowMapping) -> ProjectVersion:
        r = dict(row)
        return ProjectVersion(
            id=r["id"],
            version=r["version"],
            schema_version=r["schema_version"],
            graph=deserialize_graph(dict(r["graph"])),
            triggers=tuple(deserialize_trigger(t) for t in r["triggers"]),
            dependencies=tuple(deserialize_dependency(d) for d in r["dependencies"]),
            description=r["description"],
            published_at=_ensure_utc(r["published_at"]),
        )


class SqlAlchemyUnitOfWork(UnitOfWork):
    """One session = one transaction; commit is explicit, exit rolls back."""

    def __init__(self, database: Database) -> None:
        self._session = database.session()
        self.projects: ProjectRepository = SqlAlchemyProjectRepository(self._session)
        self.versions: ProjectVersionRepository = SqlAlchemyProjectVersionRepository(
            self._session
        )

    async def __aenter__(self) -> "SqlAlchemyUnitOfWork":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if exc_type is not None:
                await self._session.rollback()
        finally:
            await self._session.close()

    async def commit(self) -> None:
        await self._session.commit()
