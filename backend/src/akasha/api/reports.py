"""/api/v1/reports — thin HTTP adapter over ``ReportService``.

The body is the canonical export (``report_json``), so what the API returns
and what a user downloads are the same deterministic bytes.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from akasha.api.schemas import ErrorResponse
from akasha.application.reports import ExecutionReport, ReportService, report_json
from akasha.errors import ConfigurationError


def _reports(request: Request) -> ReportService:
    service: ReportService | None = request.app.state.container.reports
    if service is None:
        raise ConfigurationError("Execution persistence is not configured.")
    return service


Reports = Annotated[ReportService, Depends(_reports)]


def create_reports_router() -> APIRouter:
    # 422 too: FastAPI would otherwise document its own validation body for
    # the path parameter, but the error handler always sends ErrorResponse.
    errors: dict[int | str, dict[str, object]] = {
        code: {"model": ErrorResponse} for code in (404, 422, 500)
    }
    router = APIRouter(prefix="/reports", tags=["reports"], responses=errors)

    @router.get("/executions/{execution_id}", response_model=ExecutionReport)
    async def execution_report(execution_id: str, reports: Reports) -> Response:
        report = await reports.execution_report(execution_id)
        return Response(report_json(report), media_type="application/json")

    return router
