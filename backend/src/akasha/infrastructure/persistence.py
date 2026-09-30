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

from sqlalchemy import Insert, Update, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult, RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from akasha.application.persistence import (
    ConcurrentModificationError,
    CredentialRepository,
    EventRepository,
    ExecutionRepository,
    ProjectRepository,
    ProjectVersionRepository,
    UnitOfWork,
)
from akasha.domain.credentials import Credential, CredentialKind
from akasha.domain.event import Event, EventType
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

    async def save(self, execution: Execution) -> None:
        e = execution
        values = {
            "status": e.status.value,
            "metadata": {
                "trigger_type": e.metadata.trigger_type.value,
                "trigger_id": e.metadata.trigger_id,
                "correlation_id": e.metadata.correlation_id,
                "engine_version": e.metadata.engine_version,
                "node_versions": dict(e.metadata.node_versions),
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
    ) -> Sequence[Event]:
        stmt = select(m.events).where(m.events.c.position > after)
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
        self.credentials: CredentialRepository = SqlAlchemyCredentialRepository(
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
