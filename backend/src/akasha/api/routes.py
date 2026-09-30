"""Operational endpoints and the v1 API router."""

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from akasha import __version__
from akasha.api.credentials import create_credentials_router
from akasha.api.projects import create_projects_router
from akasha.api.reports import create_reports_router
from akasha.api.triggers import create_triggers_router
from akasha.observability.metrics import CONTENT_TYPE


class HealthResponse(BaseModel):
    status: str
    version: str


class ReadyResponse(BaseModel):
    status: str
    failures: dict[str, str]


def create_operational_router() -> APIRouter:
    """/health, /ready and /metrics at the root (not under /api/v1)."""
    router = APIRouter()

    @router.get("/health")
    async def health() -> HealthResponse:
        # Process lives. Deliberately dependency-free (§67 backend/AGENTS.md).
        return HealthResponse(status="ok", version=__version__)

    @router.get("/ready")
    async def ready(request: Request) -> Any:
        failures = await request.app.state.container.readiness_failures()
        if failures:
            content = ReadyResponse(status="not_ready", failures=failures).model_dump()
            return JSONResponse(status_code=503, content=content)
        return ReadyResponse(status="ready", failures={})

    @router.get("/metrics", include_in_schema=False)
    async def metrics(request: Request) -> Response:
        # Prometheus scrape target. Unauthenticated like /health until
        # Phase 20; it carries counts and timings only, no ids or payloads.
        content = request.app.state.container.metrics.render()
        return Response(content=content, media_type=CONTENT_TYPE)

    return router


def create_api_v1_router() -> APIRouter:
    """Business API under /api/v1."""
    router = APIRouter(prefix="/api/v1")
    router.include_router(create_projects_router())
    router.include_router(create_triggers_router())
    router.include_router(create_credentials_router())
    router.include_router(create_reports_router())
    return router
