"""/api/v1/monitoring — thin HTTP adapter over ``MonitoringService``.

- GET /monitoring/summary?since=&until= : the executions created in that
  window (at most 31 days), aggregated: status counts, durations, timeline,
  error codes, per node type performance, retries, tokens and cost.

Members see their own workflows' runs, admins every run (the owner filter of
the cross-workflow execution listing).
"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from akasha.api.auth import CurrentIdentity
from akasha.api.schemas import ErrorResponse
from akasha.application.auth import AuthorizationService
from akasha.application.monitoring import MonitoringService, MonitoringSummary
from akasha.domain.primitives import utcnow
from akasha.errors import ConfigurationError


def _monitoring(request: Request) -> MonitoringService:
    service: MonitoringService | None = request.app.state.container.monitoring
    if service is None:
        raise ConfigurationError("Execution persistence is not configured.")
    return service


Monitoring = Annotated[MonitoringService, Depends(_monitoring)]


def create_monitoring_router() -> APIRouter:
    errors: dict[int | str, dict[str, Any]] = {
        code: {"model": ErrorResponse} for code in (422, 500)
    }
    router = APIRouter(prefix="/monitoring", tags=["monitoring"], responses=errors)

    @router.get("/summary")
    async def summary(
        monitoring: Monitoring,
        identity: CurrentIdentity,
        since: Annotated[
            datetime, Query(description="Window start, inclusive, with timezone")
        ],
        until: Annotated[
            datetime | None,
            Query(description="Window end, exclusive, with timezone; default now"),
        ] = None,
    ) -> MonitoringSummary:
        return await monitoring.summary(
            since=since,
            until=until or utcnow(),
            visible_to=AuthorizationService.visible_to(identity),
        )

    return router
