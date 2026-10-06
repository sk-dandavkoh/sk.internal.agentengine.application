"""Operational endpoints and the v1 API router.

Access policy (architecture §149), in one place:

- public: /health, /ready, /docs + /openapi.json, POST /api/v1/auth/setup,
  POST /api/v1/auth/login, POST /api/v1/webhooks/...
- WS /api/v1/executions/{id}/live: authenticates and authorizes in its
  first frame or, same-origin only, by the session cookie
  (``akasha.api.live``), the same session + execution rule
- /metrics: the configured scrape token, else an admin session
- every other /api/v1 route: a session (``current_identity``: bearer
  token, or the session cookie plus the X-Akasha-CSRF header) with access to
  the workflow/execution in its path (``authorize_path``); credentials,
  users and plugins additionally need an admin (``require_admin``); the node catalog
  (/api/v1/nodes), /api/v1/system/status and the cross-workflow execution
  listings (/api/v1/executions, owner-filtered) need only a session
"""

import hmac
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from akasha import __version__
from akasha.api.auth import (
    auth_service,
    authorize_path,
    bearer_token,
    create_auth_router,
    create_users_router,
    current_identity,
    require_admin,
)
from akasha.api.credentials import create_credentials_router
from akasha.api.executions import create_executions_router
from akasha.api.live import create_live_router
from akasha.api.monitoring import create_monitoring_router
from akasha.api.nodes import create_nodes_router
from akasha.api.plugins import create_plugins_router
from akasha.api.reports import create_reports_router
from akasha.api.sharing import create_sharing_router
from akasha.api.triggers import create_triggers_router, create_webhooks_router
from akasha.api.workflows import create_workflows_router
from akasha.application.auth import AuthenticationRequiredError
from akasha.application.auth import require_admin as require_admin_identity
from akasha.observability.metrics import CONTENT_TYPE


class HealthResponse(BaseModel):
    status: str
    version: str


class ReadyResponse(BaseModel):
    status: str
    failures: dict[str, str]


class SystemStatus(BaseModel):
    """/health and /ready for API clients: the UI reaches only /api/v1."""

    version: str
    ready: bool
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

    @router.get(
        "/metrics", include_in_schema=False, dependencies=[Depends(_metrics_access)]
    )
    async def metrics(request: Request) -> Response:
        # Prometheus scrape target: counts and timings only, no ids or payloads.
        content = request.app.state.container.metrics.render()
        return Response(content=content, media_type=CONTENT_TYPE)

    return router


async def _metrics_access(request: Request) -> None:
    """Prometheus cannot log in: a configured static token lets it scrape.
    Without one, only an admin session can read the metrics."""
    token = bearer_token(request)
    if token is None:
        raise AuthenticationRequiredError("Authentication required.")
    configured = request.app.state.container.settings.metrics_token
    if configured is not None and hmac.compare_digest(
        token.encode(), configured.get_secret_value().encode()
    ):
        return
    require_admin_identity(await auth_service(request).authenticate(token))


def create_system_router() -> APIRouter:
    router = APIRouter(prefix="/system", tags=["system"])

    @router.get("/status")
    async def system_status(request: Request) -> SystemStatus:
        # Same checks and reasons /ready publishes; never a 503 here, the
        # caller asked for the status, not whether to route traffic.
        failures = await request.app.state.container.readiness_failures()
        return SystemStatus(version=__version__, ready=not failures, failures=failures)

    return router


def create_api_v1_router() -> APIRouter:
    """Business API under /api/v1. Protected unless listed as public above:
    a route added to a protected router inherits its guards."""
    router = APIRouter(prefix="/api/v1")
    router.include_router(create_auth_router())
    router.include_router(create_webhooks_router())
    router.include_router(create_live_router())

    protected = APIRouter(
        dependencies=[Depends(current_identity), Depends(authorize_path)]
    )
    protected.include_router(create_workflows_router())
    protected.include_router(create_sharing_router())
    protected.include_router(create_triggers_router())
    protected.include_router(create_executions_router())
    protected.include_router(create_nodes_router())
    protected.include_router(create_reports_router())
    protected.include_router(create_monitoring_router())
    protected.include_router(create_system_router())
    admin_only = [Depends(require_admin)]
    protected.include_router(create_credentials_router(), dependencies=admin_only)
    protected.include_router(create_users_router(), dependencies=admin_only)
    protected.include_router(create_plugins_router(), dependencies=admin_only)
    router.include_router(protected)
    return router
