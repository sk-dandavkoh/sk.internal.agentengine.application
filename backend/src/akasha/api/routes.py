"""Operational endpoints and the v1 API router."""

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from akasha import __version__


class HealthResponse(BaseModel):
    status: str
    version: str


class ReadyResponse(BaseModel):
    status: str
    failures: dict[str, str]


def create_operational_router() -> APIRouter:
    """/health and /ready at the root (not under /api/v1)."""
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

    return router


def create_api_v1_router() -> APIRouter:
    """Business API under /api/v1. Endpoints arrive in Phase 06+."""
    return APIRouter(prefix="/api/v1")
