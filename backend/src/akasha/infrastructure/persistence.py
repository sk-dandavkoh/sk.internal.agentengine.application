"""SQLAlchemy implementations of the application-layer persistence interfaces.

Domain objects are rebuilt from rows (and JSONB documents) explicitly — no
ORM identity map, no lazy loading across the boundary (architecture §79).
Repositories see an externally owned ``AsyncSession``; commits and rollbacks
are the exclusive job of ``SqlAlchemyUnitOfWork`` (backend/AGENTS.md §16).
"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, cast

from sqlalchemy import Insert, Update, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult, RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from akasha.application.persistence import (
    ConcurrentModificationError,
    ExecutionRepository,
    ProjectRepository,
    ProjectVersionRepository,
    UnitOfWork,
)
from akasha.domain.execution import (
    CancellationState,
    Execution,
    ExecutionError,
    ExecutionMetadata,
    ExecutionStatus,
    NodeExecution,
    NodeExecutionStatus,
    RetryPolicy,
    TimeoutPolicy,
)
from akasha.domain.node import NodeErrorKind
from akasha.domain.primitives import Timestamps
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
        )

    async def save(self, execution: Execution) -> None:
        e = execution
        values = {
            "status": e.status.value,
            "metadata": {
                "trigger_type": e.metadata.trigger_type.value,
                "trigger_id": e.metadata.trigger_id,
                "correlation_id": e.metadata.correlation_id,
                "engine_version": e.metadata.engine_version,
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
        doc: dict[str, Any] = json.loads(
            json.dumps(
                outputs,
                allow_nan=False,  # JSONB rejects NaN/Infinity
                default=lambda v: {"$unpersisted": type(v).__name__},
            )
        )
    except (TypeError, ValueError):  # e.g. NaN, circular data, tuple keys
        return {"$unpersisted": "value not representable as JSON"}
    return doc


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
