"""SQLAlchemy implementations of the application-layer persistence interfaces.

Domain objects are rebuilt from rows (and JSONB documents) explicitly — no
ORM identity map, no lazy loading across the boundary (architecture §79).
Repositories see an externally owned ``AsyncSession``; commits and rollbacks
are the exclusive job of ``SqlAlchemyUnitOfWork`` (backend/AGENTS.md §16).
"""

import json
from collections.abc import Collection, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, cast

from sqlalchemy import (
    Insert,
    Select,
    Update,
    delete,
    func,
    literal,
    literal_column,
    or_,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONPATH
from sqlalchemy.dialects.postgresql import Insert as PgInsert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult, RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from akasha.application.persistence import (
    AccessTokenRepository,
    ConcurrentModificationError,
    CredentialRepository,
    EventRepository,
    ExecutionRepository,
    ExecutionSample,
    ExecutionSummary,
    NodeLogRepository,
    NodeSample,
    ProjectRepository,
    ProjectSort,
    ProjectVersionRepository,
    SessionRepository,
    UnitOfWork,
    UserRepository,
)
from akasha.domain.credentials import Credential, CredentialKind
from akasha.domain.event import Event, EventType
from akasha.domain.execution import (
    TERMINAL_EXECUTION_STATUSES,
    CancellationState,
    Execution,
    ExecutionError,
    ExecutionMetadata,
    ExecutionStatus,
    LogLevel,
    NodeExecution,
    NodeExecutionStatus,
    NodeLogEntry,
    RetryPolicy,
    TimeoutPolicy,
)
from akasha.domain.identity import AccessToken, Role, User, UserSession
from akasha.domain.node import NodeErrorKind
from akasha.domain.primitives import Timestamps, is_storable
from akasha.domain.project import (
    Project,
    ProjectDraft,
    ProjectStatus,
    ProjectVersion,
)
from akasha.domain.trigger import TriggerType
from akasha.errors import DomainValidationError
from akasha.infrastructure import db_models as m
from akasha.infrastructure.database import Database
from akasha.infrastructure.graph_serialization import (
    deserialize_dependency,
    deserialize_files,
    deserialize_graph,
    deserialize_trigger,
    serialize_dependency,
    serialize_files,
    serialize_graph,
    serialize_trigger,
)


def _ensure_utc(dt: datetime) -> datetime:
    """asyncpg returns naive datetimes for timestamptz; domain expects UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


# Scans the JSONB documents in SQL: no project is loaded into memory.
_USING_PLUGIN = text(
    """
    SELECT p.id FROM projects p
    WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(p.draft_graph->'nodes') n
                  WHERE n->>'node_type' = ANY(:types))
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(p.draft_dependencies) d
                  WHERE d->>'kind' = 'plugin' AND d->>'name' = :plugin)
       OR EXISTS (
            SELECT 1 FROM project_versions v
            WHERE v.project_id = p.id
              AND (EXISTS (SELECT 1 FROM jsonb_array_elements(v.graph->'nodes') n
                           WHERE n->>'node_type' = ANY(:types))
                   OR EXISTS (SELECT 1 FROM jsonb_array_elements(v.dependencies) d
                              WHERE d->>'kind' = 'plugin' AND d->>'name' = :plugin)))
    ORDER BY p.id
    """
)


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
            "draft_files": serialize_files(project.draft.files),
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
                .values(id=project.id, owner_id=project.owner_id, **values)
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

    async def list(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
        owner_id: str | None = None,
        search: str | None = None,
        statuses: Collection[ProjectStatus] = (),
        sort: ProjectSort = ProjectSort.CREATED,
    ) -> Sequence[Project]:
        c = m.projects.c
        query = select(m.projects)
        if owner_id is not None:
            query = query.where(c.owner_id == owner_id)
        if search:
            # ponytail: unindexed substring scan; add a pg_trgm index if
            # project counts make it slow.
            literal = (
                search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            query = query.where(c.name.ilike(f"%{literal}%", escape="\\"))
        if statuses:
            query = query.where(c.status.in_([s.value for s in statuses]))
        column = {
            "created_at": c.created_at,
            "updated_at": c.updated_at,
            "name": func.lower(c.name),
        }[sort.removeprefix("-")]
        descending = sort.startswith("-")
        order = (column.desc(), c.id.desc()) if descending else (column, c.id)
        rows = (
            (
                await self._session.execute(
                    query.order_by(*order).offset(offset).limit(limit)
                )
            )
            .mappings()
            .all()
        )
        return [self._to_domain(row) for row in rows]

    async def using_plugin(
        self, plugin_id: str, node_types: Sequence[str]
    ) -> Sequence[str]:
        result = await self._session.execute(
            _USING_PLUGIN, {"plugin": plugin_id, "types": list(node_types)}
        )
        return list(result.scalars())

    @staticmethod
    def _to_domain(row: RowMapping) -> Project:
        r = dict(row)
        draft = ProjectDraft(
            graph=deserialize_graph(dict(r["draft_graph"])),
            triggers=[deserialize_trigger(t) for t in r["draft_triggers"]],
            dependencies=[deserialize_dependency(d) for d in r["draft_dependencies"]],
            description=r["description"],
            files=deserialize_files(r["draft_files"]),
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
            owner_id=r["owner_id"],
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
                files=serialize_files(version.files),
                published_by=version.published_by,
                plugin_versions=dict(version.plugin_versions),
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

    async def list_latest(
        self, *, project_id: str | None = None
    ) -> Sequence[tuple[str, ProjectVersion]]:
        # ponytail: the scheduler scans one full row per project and filters
        # triggers in Python; push trigger matching into SQL (JSONB ops) when
        # catalogs get large. Webhook/invoke resolution passes project_id.
        latest = select(
            m.project_versions.c.project_id,
            func.max(m.project_versions.c.version).label("max_version"),
        ).group_by(m.project_versions.c.project_id)
        if project_id is not None:
            latest = latest.where(m.project_versions.c.project_id == project_id)
        newest = latest.subquery()
        rows = (
            (
                await self._session.execute(
                    select(m.project_versions)
                    .join(
                        newest,
                        (m.project_versions.c.project_id == newest.c.project_id)
                        & (m.project_versions.c.version == newest.c.max_version),
                    )
                    .order_by(m.project_versions.c.project_id)
                )
            )
            .mappings()
            .all()
        )
        return [(row["project_id"], self._to_domain(row)) for row in rows]

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
            files=deserialize_files(r["files"]),
            published_by=r["published_by"],
            plugin_versions=dict(r["plugin_versions"]),
        )


def _owned[S: Select[Any]](query: S, owner_id: str | None) -> S:
    """Limit an executions query to the projects of ``owner_id`` (None: all)."""
    if owner_id is None:
        return query
    return query.join(m.projects, m.projects.c.id == m.executions.c.project_id).where(
        m.projects.c.owner_id == owner_id
    )


class SqlAlchemyExecutionRepository(ExecutionRepository):
    def __init__(
        self, session: AsyncSession, versions: ProjectVersionRepository
    ) -> None:
        self._session = session
        self._versions = versions

    async def get(self, execution_id: str) -> Execution | None:
        # FOR SHARE: the execution row and its node rows are read by two
        # statements; under READ COMMITTED a writer committing in between
        # would yield a torn aggregate (e.g. QUEUED execution with READY
        # nodes). Writers update the execution row first, so holding a share
        # lock on it makes both reads see the same committed state.
        row = (
            (
                await self._session.execute(
                    select(m.executions)
                    .where(m.executions.c.id == execution_id)
                    .with_for_update(read=True)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        version = await self._versions.get(row["project_id"], row["project_version"])
        if version is None:  # unreachable while the FK holds
            raise DomainValidationError(
                "execution references a missing project version",
                details={"execution_id": execution_id},
            )
        node_rows = (
            (
                await self._session.execute(
                    select(m.node_executions)
                    .where(m.node_executions.c.execution_id == execution_id)
                    .order_by(m.node_executions.c.node_id)
                )
            )
            .mappings()
            .all()
        )
        nodes = [_node_to_domain(r) for r in node_rows]
        return Execution(
            id=row["id"],
            project_id=row["project_id"],
            project_version=version,
            metadata=_metadata(row["metadata"]),
            retry_policy=_retry_policy(row["retry_policy"]),
            timeout_policy=_timeout_policy(row["timeout_policy"]),
            status=ExecutionStatus(row["status"]),
            node_executions={ne.node_id: ne for ne in nodes},
            error=None if row["error"] is None else _error(row["error"]),
            cancellation=None
            if row["cancellation"] is None
            else CancellationState(
                requested_at=datetime.fromisoformat(
                    row["cancellation"]["requested_at"]
                ),
                reason=row["cancellation"]["reason"],
            ),
            created_at=_ensure_utc(row["created_at"]),
            started_at=_opt_utc(row["started_at"]),
            finished_at=_opt_utc(row["finished_at"]),
            revision=row["revision"],
            input=dict(row["input"]),
        )

    async def list_summaries(
        self,
        *,
        project_id: str | None = None,
        project_version: int | None = None,
        owner_id: str | None = None,
        statuses: Collection[ExecutionStatus] = (),
        offset: int = 0,
        limit: int = 100,
    ) -> Sequence[ExecutionSummary]:
        e = m.executions.c
        p = m.projects.c
        # Inner join: versions RESTRICT project deletion, so every execution
        # has its project row.
        query = select(
            e.id,
            e.project_id,
            p.name.label("project_name"),
            e.project_version,
            e.status,
            e.metadata,
            e.created_at,
            e.started_at,
            e.finished_at,
        ).select_from(m.executions.join(m.projects, p.id == e.project_id))
        if owner_id is not None:
            query = query.where(p.owner_id == owner_id)
        if project_id is not None:
            query = query.where(e.project_id == project_id)
        if project_version is not None:
            query = query.where(e.project_version == project_version)
        if statuses:
            query = query.where(e.status.in_([s.value for s in statuses]))
        rows = (
            await self._session.execute(
                query.order_by(e.created_at.desc(), e.id.desc())
                .offset(offset)
                .limit(limit)
            )
        ).mappings()
        return [
            ExecutionSummary(
                id=r["id"],
                project_id=r["project_id"],
                project_name=r["project_name"],
                project_version=r["project_version"],
                status=ExecutionStatus(r["status"]),
                metadata=_metadata(r["metadata"]),
                created_at=_ensure_utc(r["created_at"]),
                started_at=_opt_utc(r["started_at"]),
                finished_at=_opt_utc(r["finished_at"]),
            )
            for r in rows
        ]

    async def count_by_status(
        self, *, owner_id: str | None = None
    ) -> Mapping[ExecutionStatus, int]:
        e = m.executions.c
        query = _owned(select(e.status, func.count()), owner_id).group_by(e.status)
        rows = (await self._session.execute(query)).all()
        return {ExecutionStatus(row[0]): int(row[1]) for row in rows}

    async def sample_executions(
        self, *, since: datetime, until: datetime, owner_id: str | None = None
    ) -> Sequence[ExecutionSample]:
        e = m.executions.c
        query = _owned(
            select(
                e.status,
                e.created_at,
                e.started_at,
                e.finished_at,
                e.error["code"].as_string(),
                e.error["kind"].as_string(),
            ),
            owner_id,
        ).where(e.created_at >= since, e.created_at < until)
        rows = await self._session.execute(query.order_by(e.created_at, e.id))
        return [
            ExecutionSample(
                status=ExecutionStatus(row[0]),
                created_at=_ensure_utc(row[1]),
                started_at=_opt_utc(row[2]),
                finished_at=_opt_utc(row[3]),
                error_code=row[4],
                error_kind=row[5],
            )
            for row in rows
        ]

    async def sample_node_executions(
        self, *, since: datetime, until: datetime, owner_id: str | None = None
    ) -> Sequence[NodeSample]:
        e, ne, v = m.executions.c, m.node_executions.c, m.project_versions.c
        # The node type lives in the pinned version's graph, not on the row.
        node_type = func.jsonb_path_query_first(
            v.graph,
            literal("$.nodes[*] ? (@.id == $id).node_type", JSONPATH),
            func.jsonb_build_object("id", ne.node_id),
        ).op("#>>")(literal_column("'{}'"))
        query = _owned(
            select(
                node_type,
                ne.status,
                ne.attempt,
                func.jsonb_array_length(ne.failures),
                ne.started_at,
                ne.finished_at,
                ne.outputs["metadata"],
            )
            .select_from(m.node_executions)
            .join(m.executions, e.id == ne.execution_id)
            .join(
                m.project_versions,
                (v.project_id == e.project_id) & (v.version == e.project_version),
            ),
            owner_id,
        ).where(e.created_at >= since, e.created_at < until)
        rows = await self._session.execute(query.order_by(e.id, ne.node_id))
        return [
            NodeSample(
                # Unreachable while versions are immutable; never drop a row.
                node_type=row[0] or "unknown",
                status=NodeExecutionStatus(row[1]),
                attempts=row[2],
                failed_attempts=row[3],
                started_at=_opt_utc(row[4]),
                finished_at=_opt_utc(row[5]),
                metadata=row[6],
            )
            for row in rows
        ]

    async def list_queued(
        self, *, created_before: datetime, limit: int
    ) -> Sequence[str]:
        rows = await self._session.execute(
            select(m.executions.c.id)
            .where(
                m.executions.c.status == ExecutionStatus.QUEUED.value,
                m.executions.c.created_at < created_before,
            )
            .order_by(m.executions.c.created_at, m.executions.c.id)
            .limit(limit)
        )
        return list(rows.scalars())

    async def count_unfinished(self, project_id: str) -> int:
        # NO KEY UPDATE: serializes admissions without blocking the key-share
        # locks taken by inserts that reference the project.
        await self._session.execute(
            select(m.projects.c.id)
            .where(m.projects.c.id == project_id)
            .with_for_update(key_share=True)
        )
        count = await self._session.scalar(
            select(func.count())
            .select_from(m.executions)
            .where(
                m.executions.c.project_id == project_id,
                m.executions.c.status.notin_(
                    [s.value for s in TERMINAL_EXECUTION_STATUSES]
                ),
            )
        )
        return int(count or 0)

    async def heartbeat(self, execution_id: str, *, revision: int) -> bool:
        result = cast(
            "CursorResult[Any]",
            await self._session.execute(
                m.executions.update()
                .where(
                    m.executions.c.id == execution_id,
                    m.executions.c.revision == revision,
                )
                .values(heartbeat_at=func.now())
            ),
        )
        return result.rowcount == 1

    def _stale(self, stale_after: timedelta) -> Any:
        c = m.executions.c
        return (
            c.status.in_([s.value for s in _STARTED]),
            c.heartbeat_at < func.now() - stale_after,
        )

    async def list_stale(self, *, stale_after: timedelta, limit: int) -> Sequence[str]:
        rows = await self._session.execute(
            select(m.executions.c.id)
            .where(*self._stale(stale_after))
            .order_by(m.executions.c.heartbeat_at, m.executions.c.id)
            .limit(limit)
        )
        return list(rows.scalars())

    async def claim_stale(self, execution_id: str, *, stale_after: timedelta) -> bool:
        row = await self._session.execute(
            select(m.executions.c.id)
            .where(m.executions.c.id == execution_id, *self._stale(stale_after))
            .with_for_update(skip_locked=True)
        )
        return row.scalar_one_or_none() is not None

    async def save(self, execution: Execution) -> None:
        e = execution
        values = {
            "status": e.status.value,
            # Every write proves the writer alive (see ``heartbeat``).
            "heartbeat_at": func.now(),
            "metadata": {
                "trigger_type": e.metadata.trigger_type.value,
                "trigger_id": e.metadata.trigger_id,
                "correlation_id": e.metadata.correlation_id,
                "engine_version": e.metadata.engine_version,
                "node_versions": dict(e.metadata.node_versions),
                "trigger_depth": e.metadata.trigger_depth,
            },
            "retry_policy": {
                "max_attempts": e.retry_policy.max_attempts,
                "initial_backoff_s": e.retry_policy.initial_backoff.total_seconds(),
                "backoff_multiplier": e.retry_policy.backoff_multiplier,
                "max_backoff_s": e.retry_policy.max_backoff.total_seconds(),
                "retryable_kinds": sorted(
                    k.value for k in e.retry_policy.retryable_kinds
                ),
            },
            "timeout_policy": {
                "execution_timeout_s": _seconds(e.timeout_policy.execution_timeout),
                "node_timeout_s": _seconds(e.timeout_policy.node_timeout),
            },
            "error": None if e.error is None else _error_doc(e.error),
            "cancellation": None
            if e.cancellation is None
            else {
                "requested_at": e.cancellation.requested_at.isoformat(),
                "reason": e.cancellation.reason,
            },
            "revision": e.revision + 1,
            "created_at": e.created_at,
            "started_at": e.started_at,
            "finished_at": e.finished_at,
        }
        # Same single-statement CAS as projects (see there): a second writer
        # holding a stale revision loses instead of interleaving state.
        if e.revision == 0:
            stmt: Insert | Update = (
                pg_insert(m.executions)
                .values(
                    id=e.id,
                    project_id=e.project_id,
                    project_version=e.project_version.version,
                    # Immutable after creation: written by the insert only.
                    input=e.input,
                    **values,
                )
                .on_conflict_do_nothing(index_elements=[m.executions.c.id])
            )
        else:
            stmt = (
                m.executions.update()
                .where(m.executions.c.id == e.id, m.executions.c.revision == e.revision)
                .values(**values)
            )
        result = cast("CursorResult[Any]", await self._session.execute(stmt))
        if result.rowcount != 1:
            raise ConcurrentModificationError(
                "The execution was modified concurrently; reload and retry.",
                details={"execution_id": e.id},
            )
        rows = [_node_row(e.id, ne) for ne in e.node_executions.values()]
        if rows:
            # ponytail: rewrites every node row per save (O(nodes) per
            # transition); track dirty nodes when graphs get large (Phase 24).
            upsert = pg_insert(m.node_executions).values(rows)
            await self._session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[m.node_executions.c.id],
                    set_={
                        col: upsert.excluded[col]
                        for col in rows[0]
                        if col not in ("id", "execution_id", "node_id")
                    },
                )
            )
        e.revision += 1


_STARTED = (ExecutionStatus.RUNNING, ExecutionStatus.PAUSED, ExecutionStatus.WAITING)


def _opt_utc(dt: datetime | None) -> datetime | None:
    return None if dt is None else _ensure_utc(dt)


def _seconds(td: timedelta | None) -> float | None:
    return None if td is None else td.total_seconds()


def _opt_timedelta(seconds: float | None) -> timedelta | None:
    return None if seconds is None else timedelta(seconds=seconds)


def _error_doc(error: ExecutionError) -> dict[str, Any]:
    return {
        "code": error.code,
        "message": error.message,
        "kind": error.kind.value,
        "node_id": error.node_id,
        "details": dict(error.details),
    }


def _error(doc: dict[str, Any]) -> ExecutionError:
    return ExecutionError(
        code=doc["code"],
        message=doc["message"],
        kind=NodeErrorKind(doc["kind"]),
        node_id=doc["node_id"],
        details=doc["details"],
    )


def _metadata(doc: dict[str, Any]) -> ExecutionMetadata:
    return ExecutionMetadata(
        trigger_type=TriggerType(doc["trigger_type"]),
        trigger_id=doc["trigger_id"],
        correlation_id=doc["correlation_id"],
        engine_version=doc["engine_version"],
        # Absent in rows written before Phase 09 (no registry existed then).
        node_versions=doc.get("node_versions", {}),
        # Absent in rows written before event triggers fired.
        trigger_depth=doc.get("trigger_depth", 0),
    )


def _retry_policy(doc: dict[str, Any]) -> RetryPolicy:
    return RetryPolicy(
        max_attempts=doc["max_attempts"],
        initial_backoff=timedelta(seconds=doc["initial_backoff_s"]),
        backoff_multiplier=doc["backoff_multiplier"],
        max_backoff=timedelta(seconds=doc["max_backoff_s"]),
        retryable_kinds=frozenset(NodeErrorKind(k) for k in doc["retryable_kinds"]),
    )


def _timeout_policy(doc: dict[str, Any]) -> TimeoutPolicy:
    return TimeoutPolicy(
        execution_timeout=_opt_timedelta(doc["execution_timeout_s"]),
        node_timeout=_opt_timedelta(doc["node_timeout_s"]),
    )


def _persistable(outputs: dict[str, Any] | None) -> dict[str, Any] | None:
    """Node outputs as JSONB. Values JSON cannot hold (bytes, objects) are
    stored as a type stub — persisted outputs are an inspection record, the
    runtime propagates the in-memory values."""
    if outputs is None:
        return None
    try:
        text = json.dumps(
            outputs,
            allow_nan=False,  # JSONB rejects NaN/Infinity
            default=lambda v: {"$unpersisted": type(v).__name__},
        )
    except (TypeError, ValueError):  # e.g. NaN, circular data, tuple keys
        return dict(_UNPERSISTED)
    doc: dict[str, Any] = json.loads(text)
    # NUL characters and lone surrogates: JSON can hold them, JSONB cannot.
    return doc if is_storable(doc) else dict(_UNPERSISTED)


_UNPERSISTED = {"$unpersisted": "value not representable as JSON"}


def _node_row(execution_id: str, ne: NodeExecution) -> dict[str, Any]:
    return {
        "id": ne.id,
        "execution_id": execution_id,
        "node_id": ne.node_id,
        "status": ne.status.value,
        "attempt": ne.attempt,
        "failures": [_error_doc(f) for f in ne.failures],
        "outputs": _persistable(ne.outputs),
        "retry_at": ne.retry_at,
        "started_at": ne.started_at,
        "finished_at": ne.finished_at,
    }


def _node_to_domain(row: RowMapping) -> NodeExecution:
    return NodeExecution(
        id=row["id"],
        node_id=row["node_id"],
        status=NodeExecutionStatus(row["status"]),
        attempt=row["attempt"],
        failures=[_error(f) for f in row["failures"]],
        retry_at=_opt_utc(row["retry_at"]),
        started_at=_opt_utc(row["started_at"]),
        finished_at=_opt_utc(row["finished_at"]),
        outputs=None if row["outputs"] is None else dict(row["outputs"]),
    )


class SqlAlchemyEventRepository(EventRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, events: Sequence[Event]) -> None:
        # One statement per event, in order: identity values are then drawn
        # in event order, which is what makes ``position`` the stream order.
        # ponytail: N round trips per save (a terminal cascade emits one
        # event per unfinished node); batch when large graphs make it show.
        for e in events:
            await self._session.execute(
                pg_insert(m.events)
                .values(
                    event_id=e.event_id,
                    event_type=e.event_type.value,
                    schema_version=e.schema_version,
                    occurred_at=e.occurred_at,
                    project_id=e.project_id,
                    execution_id=e.execution_id,
                    node_id=e.node_id,
                    project_version=e.project_version,
                    correlation_id=e.correlation_id,
                    payload=dict(e.payload),
                )
                # Idempotent: a re-appended event keeps its first row.
                .on_conflict_do_nothing(index_elements=[m.events.c.event_id])
            )

    async def list(
        self,
        *,
        execution_id: str | None = None,
        project_id: str | None = None,
        after: int = 0,
        limit: int = 100,
        positions: Collection[int] | None = None,
    ) -> Sequence[Event]:
        stmt = select(m.events).where(m.events.c.position > after)
        if positions is not None:
            stmt = stmt.where(m.events.c.position.in_(list(positions)))
        if execution_id is not None:
            stmt = stmt.where(m.events.c.execution_id == execution_id)
        if project_id is not None:
            stmt = stmt.where(m.events.c.project_id == project_id)
        rows = (
            (
                await self._session.execute(
                    stmt.order_by(m.events.c.position).limit(limit)
                )
            )
            .mappings()
            .all()
        )
        return [
            Event(
                event_id=r["event_id"],
                event_type=EventType(r["event_type"]),
                schema_version=r["schema_version"],
                occurred_at=_ensure_utc(r["occurred_at"]),
                project_id=r["project_id"],
                execution_id=r["execution_id"],
                node_id=r["node_id"],
                project_version=r["project_version"],
                correlation_id=r["correlation_id"],
                payload=dict(r["payload"]),
                position=r["position"],
            )
            for r in rows
        ]

    async def max_position(self) -> int:
        result = await self._session.execute(
            select(func.coalesce(func.max(m.events.c.position), 0))
        )
        return int(result.scalar_one())


class SqlAlchemyNodeLogRepository(NodeLogRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, entries: Sequence[NodeLogEntry]) -> None:
        if not entries:
            return
        # executemany runs the rows one after another in list order, so the
        # identity values (``position``) follow the recorded order.
        await self._session.execute(
            m.node_logs.insert(),
            [
                {
                    "execution_id": e.execution_id,
                    "node_id": e.node_id,
                    "attempt": e.attempt,
                    "level": e.level.value,
                    "message": e.message,
                    "logged_at": e.logged_at,
                    "fields": dict(e.fields),
                }
                for e in entries
            ],
        )

    async def list(
        self,
        execution_id: str,
        *,
        after: int = 0,
        limit: int = 100,
        node_id: str | None = None,
        levels: Collection[LogLevel] = (),
        contains: str | None = None,
    ) -> Sequence[NodeLogEntry]:
        t = m.node_logs
        stmt = select(t).where(t.c.execution_id == execution_id, t.c.position > after)
        if node_id is not None:
            stmt = stmt.where(t.c.node_id == node_id)
        if levels:
            stmt = stmt.where(t.c.level.in_(sorted(lv.value for lv in levels)))
        if contains:
            # ponytail: sequential ILIKE over one execution's lines (bounded
            # per attempt); a trigram index if searches get slow.
            stmt = stmt.where(t.c.message.icontains(contains, autoescape=True))
        rows = (
            (await self._session.execute(stmt.order_by(t.c.position).limit(limit)))
            .mappings()
            .all()
        )
        return [
            NodeLogEntry(
                execution_id=r["execution_id"],
                node_id=r["node_id"],
                attempt=r["attempt"],
                level=LogLevel(r["level"]),
                message=r["message"],
                logged_at=_ensure_utc(r["logged_at"]),
                fields=dict(r["fields"]),
                position=r["position"],
            )
            for r in rows
        ]


class SqlAlchemyCredentialRepository(CredentialRepository):
    """Metadata reads never select ``ciphertext``: only resolution loads it."""

    _META = (
        m.credentials.c.name,
        m.credentials.c.kind,
        m.credentials.c.description,
        m.credentials.c.created_at,
        m.credentials.c.updated_at,
    )

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, name: str) -> Credential | None:
        row = (
            (
                await self._session.execute(
                    select(*self._META).where(m.credentials.c.name == name)
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else self._to_domain(row)

    async def get_ciphertext(self, name: str) -> bytes | None:
        value = await self._session.scalar(
            select(m.credentials.c.ciphertext).where(m.credentials.c.name == name)
        )
        return None if value is None else bytes(value)

    async def add(self, credential: Credential, ciphertext: bytes) -> bool:
        result = cast(
            "CursorResult[Any]",
            await self._session.execute(
                pg_insert(m.credentials)
                .values(
                    name=credential.name,
                    kind=credential.kind.value,
                    description=credential.description,
                    ciphertext=ciphertext,
                    created_at=credential.timestamps.created_at,
                    updated_at=credential.timestamps.updated_at,
                )
                .on_conflict_do_nothing(index_elements=[m.credentials.c.name])
            ),
        )
        return result.rowcount == 1

    async def update(self, credential: Credential, ciphertext: bytes | None) -> bool:
        values: dict[str, Any] = {
            "description": credential.description,
            "updated_at": credential.timestamps.updated_at,
        }
        if ciphertext is not None:
            values["ciphertext"] = ciphertext
        result = cast(
            "CursorResult[Any]",
            await self._session.execute(
                m.credentials.update()
                .where(m.credentials.c.name == credential.name)
                .values(**values)
            ),
        )
        return result.rowcount == 1

    async def delete(self, name: str) -> bool:
        result = cast(
            "CursorResult[Any]",
            await self._session.execute(
                delete(m.credentials).where(m.credentials.c.name == name)
            ),
        )
        return result.rowcount > 0

    async def list(self, *, offset: int = 0, limit: int = 100) -> Sequence[Credential]:
        rows = (
            (
                await self._session.execute(
                    select(*self._META)
                    .order_by(m.credentials.c.name)
                    .offset(offset)
                    .limit(limit)
                )
            )
            .mappings()
            .all()
        )
        return [self._to_domain(row) for row in rows]

    @staticmethod
    def _to_domain(row: RowMapping) -> Credential:
        return Credential(
            name=row["name"],
            kind=CredentialKind(row["kind"]),
            description=row["description"],
            timestamps=Timestamps(
                created_at=_ensure_utc(row["created_at"]),
                updated_at=_ensure_utc(row["updated_at"]),
            ),
        )


def _user(row: RowMapping) -> User:
    return User(
        id=row["id"],
        username=row["username"],
        role=Role(row["role"]),
        disabled=row["disabled"],
        display_name=row["display_name"],
        last_login_at=(
            None if row["last_login_at"] is None else _ensure_utc(row["last_login_at"])
        ),
        timestamps=Timestamps(
            created_at=_ensure_utc(row["created_at"]),
            updated_at=_ensure_utc(row["updated_at"]),
        ),
    )


class SqlAlchemyUserRepository(UserRepository):
    # Every column but the hash: only get_with_password reads it.
    _PUBLIC = tuple(c for c in m.users.c if c.name != "password_hash")

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: str) -> User | None:
        row = (
            (
                await self._session.execute(
                    select(*self._PUBLIC).where(m.users.c.id == user_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _user(row)

    async def get_with_password(self, username: str) -> tuple[User, str] | None:
        row = (
            (
                await self._session.execute(
                    select(m.users).where(m.users.c.username == username)
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else (_user(row), row["password_hash"])

    def _insert(self, user: User, password_hash: str) -> PgInsert:
        return pg_insert(m.users).values(
            id=user.id,
            username=user.username,
            password_hash=password_hash,
            role=user.role.value,
            disabled=user.disabled,
            display_name=user.display_name,
            created_at=user.timestamps.created_at,
            updated_at=user.timestamps.updated_at,
        )

    async def add(self, user: User, password_hash: str) -> bool:
        stmt = self._insert(user, password_hash).on_conflict_do_nothing(
            index_elements=[m.users.c.username]
        )
        result = cast("CursorResult[Any]", await self._session.execute(stmt))
        return result.rowcount == 1

    async def add_first(self, user: User, password_hash: str) -> bool:
        # SHARE ROW EXCLUSIVE conflicts with itself and with inserts: a
        # concurrent setup waits here, then sees this user and backs off.
        await self._session.execute(
            text("LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE")
        )
        if await self._session.scalar(select(func.count()).select_from(m.users)):
            return False
        await self._session.execute(self._insert(user, password_hash))
        return True

    async def update(self, user: User, password_hash: str | None = None) -> bool:
        values: dict[str, Any] = {
            "disabled": user.disabled,
            "role": user.role.value,
            "display_name": user.display_name,
            "updated_at": user.timestamps.updated_at,
        }
        if password_hash is not None:
            values["password_hash"] = password_hash
        result = cast(
            "CursorResult[Any]",
            await self._session.execute(
                m.users.update().where(m.users.c.id == user.id).values(**values)
            ),
        )
        return result.rowcount == 1

    async def lock_active_admins(self) -> int:
        rows = await self._session.execute(
            select(m.users.c.id)
            .where(m.users.c.role == Role.ADMIN.value, m.users.c.disabled.is_(False))
            .with_for_update()
        )
        return len(rows.all())

    async def record_login(self, user_id: str, at: datetime) -> None:
        await self._session.execute(
            m.users.update().where(m.users.c.id == user_id).values(last_login_at=at)
        )

    async def list(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
        search: str | None = None,
        role: Role | None = None,
        disabled: bool | None = None,
    ) -> Sequence[User]:
        c = m.users.c
        query = select(*self._PUBLIC)
        if search:
            # ponytail: unindexed substring scan, fine for self-hosted user
            # counts; add a pg_trgm index if installations grow large.
            query = query.where(
                or_(
                    c.username.icontains(search, autoescape=True),
                    c.display_name.icontains(search, autoescape=True),
                )
            )
        if role is not None:
            query = query.where(c.role == role.value)
        if disabled is not None:
            query = query.where(c.disabled.is_(disabled))
        rows = (
            (
                await self._session.execute(
                    query.order_by(c.username).offset(offset).limit(limit)
                )
            )
            .mappings()
            .all()
        )
        return [_user(row) for row in rows]


class SqlAlchemySessionRepository(SessionRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, session: UserSession) -> None:
        await self._session.execute(
            m.sessions.insert().values(
                token_hash=session.token_hash,
                user_id=session.user_id,
                created_at=session.created_at,
                expires_at=session.expires_at,
            )
        )

    async def get_active(
        self, token_hash: str, now: datetime
    ) -> tuple[UserSession, User] | None:
        s, u = m.sessions, m.users
        row = (
            (
                await self._session.execute(
                    select(
                        s.c.token_hash,
                        s.c.user_id,
                        s.c.created_at.label("session_created_at"),
                        s.c.expires_at,
                        *SqlAlchemyUserRepository._PUBLIC,
                    )
                    .join(u, u.c.id == s.c.user_id)
                    .where(
                        s.c.token_hash == token_hash,
                        s.c.expires_at > now,
                        u.c.disabled.is_(False),
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        session = UserSession(
            token_hash=row["token_hash"],
            user_id=row["user_id"],
            created_at=_ensure_utc(row["session_created_at"]),
            expires_at=_ensure_utc(row["expires_at"]),
        )
        return session, _user(row)

    async def delete(self, token_hash: str) -> None:
        await self._session.execute(
            delete(m.sessions).where(m.sessions.c.token_hash == token_hash)
        )

    async def delete_for_user(self, user_id: str, *, keep: str | None = None) -> None:
        stmt = delete(m.sessions).where(m.sessions.c.user_id == user_id)
        if keep is not None:
            stmt = stmt.where(m.sessions.c.token_hash != keep)
        await self._session.execute(stmt)

    async def delete_expired(self, now: datetime) -> None:
        await self._session.execute(
            delete(m.sessions).where(m.sessions.c.expires_at <= now)
        )


class SqlAlchemyAccessTokenRepository(AccessTokenRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, token: AccessToken) -> None:
        await self._session.execute(
            m.access_tokens.insert().values(
                id=token.id,
                token_hash=token.token_hash,
                user_id=token.user_id,
                name=token.name,
                hint=token.hint,
                created_at=token.created_at,
                expires_at=token.expires_at,
                last_used_at=token.last_used_at,
            )
        )

    async def get_active(
        self, token_hash: str, now: datetime
    ) -> tuple[AccessToken, User] | None:
        t, u = m.access_tokens, m.users
        row = (
            (
                await self._session.execute(
                    select(
                        t.c.id.label("token_id"),
                        t.c.token_hash,
                        t.c.user_id,
                        t.c.name,
                        t.c.hint,
                        t.c.created_at.label("token_created_at"),
                        t.c.expires_at,
                        t.c.last_used_at,
                        *SqlAlchemyUserRepository._PUBLIC,
                    )
                    .join(u, u.c.id == t.c.user_id)
                    .where(
                        t.c.token_hash == token_hash,
                        or_(t.c.expires_at.is_(None), t.c.expires_at > now),
                        u.c.disabled.is_(False),
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        token = _access_token({**row, "id": row["token_id"]}, "token_created_at")
        return token, _user(row)

    async def list_for_user(self, user_id: str) -> Sequence[AccessToken]:
        t = m.access_tokens
        rows = (
            (
                await self._session.execute(
                    select(t)
                    .where(t.c.user_id == user_id)
                    .order_by(t.c.created_at.desc(), t.c.id)
                )
            )
            .mappings()
            .all()
        )
        return [_access_token(row, "created_at") for row in rows]

    async def delete(self, token_id: str, *, user_id: str) -> bool:
        t = m.access_tokens
        result = cast(
            "CursorResult[Any]",
            await self._session.execute(
                delete(t).where(t.c.id == token_id, t.c.user_id == user_id)
            ),
        )
        return result.rowcount == 1

    async def delete_for_user(self, user_id: str) -> None:
        t = m.access_tokens
        await self._session.execute(delete(t).where(t.c.user_id == user_id))

    async def mark_used(
        self, token_id: str, now: datetime, *, every: timedelta
    ) -> None:
        t = m.access_tokens
        await self._session.execute(
            t.update()
            .where(
                t.c.id == token_id,
                or_(t.c.last_used_at.is_(None), t.c.last_used_at <= now - every),
            )
            .values(last_used_at=now)
        )


def _access_token(row: Mapping[Any, Any], created: str) -> AccessToken:
    def utc(value: datetime | None) -> datetime | None:
        return None if value is None else _ensure_utc(value)

    return AccessToken(
        id=row["id"],
        user_id=row["user_id"],
        name=row["name"],
        token_hash=row["token_hash"],
        hint=row["hint"],
        created_at=_ensure_utc(row[created]),
        expires_at=utc(row["expires_at"]),
        last_used_at=utc(row["last_used_at"]),
    )


class SqlAlchemyUnitOfWork(UnitOfWork):
    """One session = one transaction; commit is explicit, exit rolls back."""

    def __init__(self, database: Database) -> None:
        self._session = database.session()
        self.projects: ProjectRepository = SqlAlchemyProjectRepository(self._session)
        self.versions: ProjectVersionRepository = SqlAlchemyProjectVersionRepository(
            self._session
        )
        self.executions: ExecutionRepository = SqlAlchemyExecutionRepository(
            self._session, self.versions
        )
        self.events: EventRepository = SqlAlchemyEventRepository(self._session)
        self.node_logs: NodeLogRepository = SqlAlchemyNodeLogRepository(self._session)
        self.credentials: CredentialRepository = SqlAlchemyCredentialRepository(
            self._session
        )
        self.users: UserRepository = SqlAlchemyUserRepository(self._session)
        self.sessions: SessionRepository = SqlAlchemySessionRepository(self._session)
        self.access_tokens: AccessTokenRepository = SqlAlchemyAccessTokenRepository(
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
