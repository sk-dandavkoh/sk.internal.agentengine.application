"""Execution inspection and control: thin HTTP adapters (architecture §22).

- GET  /executions?status=&offset=&limit= : runs across the caller's
  projects (all for an admin), newest first, optionally by status
- GET  /executions/status-counts : how many of those runs are in each status
- GET  /projects/{project_id}/executions?version= : the project's runs,
  newest first, optionally of one published version
- GET  /executions/{execution_id} : one run's state
- GET  /executions/{execution_id}/events?after=&limit= : its event history
- GET  /executions/{execution_id}/logs?after=&limit=&node_id=&level=&q= : what
  its nodes logged (redacted, bounded per attempt)
- POST /executions/{execution_id}/cancel : request cooperative cancellation

Where the path carries the project or execution id, ``authorize_path``
limits the route to callers who may see that project; the cross-project
routes apply the same rule through the owner filter. Full node-level detail
(outputs, errors, usage, cost) is the report: GET /reports/executions/{id}.
"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, Request, status
from pydantic import BaseModel, Field, JsonValue

from akasha.api.auth import CurrentIdentity
from akasha.api.project_schemas import PageInfo
from akasha.api.projects import Limit, Offset
from akasha.api.schemas import ErrorResponse
from akasha.api.trigger_schemas import ExecutionDetail, ExecutionListItem
from akasha.application.auth import AuthorizationService
from akasha.application.executions import ExecutionService
from akasha.application.persistence import ExecutionSummary
from akasha.application.projects import MAX_PAGE_SIZE, Page
from akasha.domain.event import Event
from akasha.domain.execution import ExecutionStatus, LogLevel, NodeLogEntry
from akasha.errors import ConfigurationError
from akasha.runtime.runtime import ExecutionRuntime
from akasha.security.redaction import redact, redact_text


def _executions(request: Request) -> ExecutionService:
    service: ExecutionService | None = request.app.state.container.executions
    if service is None:
        raise ConfigurationError("Execution persistence is not configured.")
    return service


def _runtime(request: Request) -> ExecutionRuntime:
    runtime: ExecutionRuntime | None = request.app.state.container.runtime
    if runtime is None:
        raise ConfigurationError("Execution persistence is not configured.")
    return runtime


Executions = Annotated[ExecutionService, Depends(_executions)]
Runtime = Annotated[ExecutionRuntime, Depends(_runtime)]


class ExecutionPage(BaseModel):
    items: list[ExecutionListItem]
    page: PageInfo

    @classmethod
    def from_page(cls, page: Page[ExecutionSummary]) -> "ExecutionPage":
        return cls(
            items=[ExecutionListItem.from_summary(s) for s in page.items],
            page=PageInfo(offset=page.offset, limit=page.limit, has_more=page.has_more),
        )


class EventOut(BaseModel):
    """The stored event (architecture §18), payload redacted on the way out
    like every output channel (observability §12)."""

    event_id: str
    event_type: str
    schema_version: int
    position: int
    occurred_at: datetime
    project_id: str
    project_version: int | None
    execution_id: str | None
    node_id: str | None
    correlation_id: str | None
    payload: dict[str, JsonValue]

    @classmethod
    def from_domain(cls, event: Event) -> "EventOut":
        payload: Any = redact(dict(event.payload))
        return cls(
            event_id=event.event_id,
            event_type=event.event_type.value,
            schema_version=event.schema_version,
            # Stored events always carry their position.
            position=event.position or 0,
            occurred_at=event.occurred_at,
            project_id=event.project_id,
            project_version=event.project_version,
            execution_id=event.execution_id,
            node_id=event.node_id,
            correlation_id=event.correlation_id,
            payload=payload,
        )


class EventPage(BaseModel):
    """Pass ``next_after`` as ``after`` to continue: the next page while
    ``has_more``, later events of a running execution otherwise."""

    items: list[EventOut]
    next_after: int
    has_more: bool


class LogLineOut(BaseModel):
    """A stored node log line. Redacted again on the way out, like events:
    the runtime redacted it when it was captured, this guards older rows
    against rules added since."""

    position: int
    node_id: str
    attempt: int
    level: LogLevel
    message: str
    logged_at: datetime
    fields: dict[str, JsonValue]

    @classmethod
    def from_domain(cls, entry: NodeLogEntry) -> "LogLineOut":
        fields: Any = redact(dict(entry.fields))
        return cls(
            position=entry.position or 0,  # stored lines carry their position
            node_id=entry.node_id,
            attempt=entry.attempt,
            level=entry.level,
            message=redact_text(entry.message),
            logged_at=entry.logged_at,
            fields=fields,
        )


class LogPage(BaseModel):
    """Paged like ``EventPage``: pass ``next_after`` as ``after``."""

    items: list[LogLineOut]
    next_after: int
    has_more: bool


class StatusCounts(BaseModel):
    """Every execution status with its count (zero included)."""

    counts: dict[ExecutionStatus, int]


class CancelRequest(BaseModel):
    reason: str = Field(default="cancelled through the API", max_length=500)


def create_executions_router() -> APIRouter:
    errors: dict[int | str, dict[str, Any]] = {
        code: {"model": ErrorResponse} for code in (404, 409, 422, 500)
    }
    router = APIRouter(tags=["executions"], responses=errors)

    @router.get("/executions")
    async def list_recent_executions(
        svc: Executions,
        identity: CurrentIdentity,
        status: Annotated[list[ExecutionStatus] | None, Query()] = None,
        offset: Offset = 0,
        limit: Limit = 50,
    ) -> ExecutionPage:
        page = await svc.list_recent(
            owner_id=AuthorizationService.owner_filter(identity),
            statuses=status or (),
            offset=offset,
            limit=limit,
        )
        return ExecutionPage.from_page(page)

    # Registered before /executions/{execution_id}, which would match it too.
    @router.get("/executions/status-counts")
    async def count_executions(
        svc: Executions, identity: CurrentIdentity
    ) -> StatusCounts:
        owner = AuthorizationService.owner_filter(identity)
        return StatusCounts(counts=await svc.count_by_status(owner_id=owner))

    @router.get("/projects/{project_id}/executions")
    async def list_executions(
        project_id: str,
        svc: Executions,
        offset: Offset = 0,
        limit: Limit = 50,
        version: Annotated[
            int | None, Query(ge=1, description="Only runs of this version")
        ] = None,
    ) -> ExecutionPage:
        page = await svc.list_executions(
            project_id, version=version, offset=offset, limit=limit
        )
        return ExecutionPage.from_page(page)

    @router.get("/executions/{execution_id}")
    async def get_execution(execution_id: str, svc: Executions) -> ExecutionDetail:
        return ExecutionDetail.from_domain(await svc.get_execution(execution_id))

    @router.get("/executions/{execution_id}/events")
    async def list_events(
        execution_id: str,
        svc: Executions,
        after: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 100,
    ) -> EventPage:
        page = await svc.list_events(execution_id, after=after, limit=limit)
        items = [EventOut.from_domain(e) for e in page.items]
        return EventPage(
            items=items,
            next_after=items[-1].position if items else after,
            has_more=page.has_more,
        )

    @router.get("/executions/{execution_id}/logs")
    async def list_logs(
        execution_id: str,
        svc: Executions,
        after: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 100,
        node_id: Annotated[
            str | None, Query(description="Only lines of this node")
        ] = None,
        level: Annotated[
            LogLevel, Query(description="Only lines at or above this level")
        ] = LogLevel.DEBUG,
        q: Annotated[
            str | None,
            Query(
                min_length=1,
                max_length=200,
                description="Only lines whose message contains this text"
                " (case-insensitive)",
            ),
        ] = None,
    ) -> LogPage:
        page = await svc.list_logs(
            execution_id,
            after=after,
            limit=limit,
            node_id=node_id,
            min_level=level,
            contains=q,
        )
        items = [LogLineOut.from_domain(e) for e in page.items]
        return LogPage(
            items=items,
            next_after=items[-1].position if items else after,
            has_more=page.has_more,
        )

    @router.post(
        "/executions/{execution_id}/cancel", status_code=status.HTTP_202_ACCEPTED
    )
    async def cancel_execution(
        execution_id: str,
        svc: Executions,
        runtime: Runtime,
        body: Annotated[CancelRequest | None, Body()] = None,
    ) -> ExecutionDetail:
        """Accepted, not finished: the owning worker stops cooperatively at
        its next heartbeat. A finished execution answers 409
        INVALID_STATE_TRANSITION."""
        reason = (body or CancelRequest()).reason
        await runtime.cancel(execution_id, reason=reason)
        return ExecutionDetail.from_domain(await svc.get_execution(execution_id))

    return router
