"""Trigger mechanism endpoints: thin HTTP adapters over application services.

- POST /projects/{project_id}/versions/{version}/execute : manual run (§126)
- POST /projects/{project_id}/triggers/{trigger_id}/invoke : configured
  MANUAL/API triggers
- POST /webhooks/{project_id}/{trigger_id} : external webhook call (§127)
- GET  /executions/{execution_id} : observe the created execution

Webhooks are the one public route (their own router): external systems
call them without a user session. Everything else needs a session with
access to the project or execution in the path.

Trigger ids are unique only within a project, so every trigger URL carries
the project id. All four endpoints end in ``TriggerService`` →
``ExecutionService`` (§32). Nothing here runs a graph; webhooks in particular
never execute synchronously (§127). The execution is recorded as QUEUED
and dispatched to the workers (Phase 22): the response never waits for the
run, and the run does not depend on the request.
"""

import json
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Path, Request, status
from pydantic import JsonValue

from akasha.api.schemas import ErrorResponse, request_id
from akasha.api.trigger_schemas import ExecutionDetail
from akasha.application.executions import ExecutionService
from akasha.application.triggers import (
    INVOCABLE_TYPES,
    TriggerNotFoundError,
    TriggerRegistry,
    TriggerService,
)
from akasha.domain.trigger import TriggerContext, TriggerType
from akasha.errors import AkashaError, ConfigurationError, DomainValidationError


class PayloadTooLargeError(AkashaError):
    code = "PAYLOAD_TOO_LARGE"
    http_status = 413


def _triggers(request: Request) -> TriggerService:
    service: TriggerService | None = request.app.state.container.triggers
    if service is None:
        raise ConfigurationError("Trigger persistence is not configured.")
    return service


def _registry(request: Request) -> TriggerRegistry:
    registry: TriggerRegistry | None = request.app.state.container.trigger_registry
    if registry is None:
        raise ConfigurationError("Trigger persistence is not configured.")
    return registry


def _executions(request: Request) -> ExecutionService:
    service: ExecutionService | None = request.app.state.container.executions
    if service is None:
        raise ConfigurationError("Execution persistence is not configured.")
    return service


Triggers = Annotated[TriggerService, Depends(_triggers)]
Registry = Annotated[TriggerRegistry, Depends(_registry)]
Executions = Annotated[ExecutionService, Depends(_executions)]
VersionNumber = Annotated[int, Path(ge=1)]
JsonObject = Annotated[dict[str, JsonValue] | None, Body()]

# Allowlist, not denylist: headers land in the trigger context for
# observability/validation and must never smuggle caller secrets (§29/§49).
_CONTEXT_HEADERS = frozenset({"content-type", "user-agent", "x-request-id"})


def _context(
    request: Request, trigger_type: TriggerType, payload: dict[str, Any]
) -> TriggerContext:
    return TriggerContext(
        trigger_type=trigger_type,
        payload=payload,
        headers={
            name: value
            for name, value in request.headers.items()
            if name.lower() in _CONTEXT_HEADERS
        },
        source=trigger_type.value,
        metadata={"correlation_id": request_id(request)},
    )


def _not_found(project_id: str, trigger_id: str) -> TriggerNotFoundError:
    return TriggerNotFoundError(
        "Trigger not found.",
        details={"project_id": project_id, "trigger_id": trigger_id},
    )


def create_triggers_router() -> APIRouter:
    errors: dict[int | str, dict[str, Any]] = {
        code: {"model": ErrorResponse} for code in (404, 409, 413, 422, 500)
    }
    router = APIRouter(tags=["triggers"], responses=errors)

    @router.post(
        "/projects/{project_id}/versions/{version}/execute",
        status_code=status.HTTP_201_CREATED,
    )
    async def execute_version(
        project_id: str,
        version: VersionNumber,
        request: Request,
        svc: Triggers,
        body: JsonObject = None,
    ) -> ExecutionDetail:
        execution = await svc.fire_manual(
            project_id,
            version,
            input=body,
            correlation_id=request_id(request),
        )
        return ExecutionDetail.from_domain(execution)

    @router.post(
        "/projects/{project_id}/triggers/{trigger_id}/invoke",
        status_code=status.HTTP_201_CREATED,
    )
    async def invoke_trigger(
        project_id: str,
        trigger_id: str,
        request: Request,
        registry: Registry,
        svc: Triggers,
        body: JsonObject = None,
    ) -> ExecutionDetail:
        resolved = await registry.resolve(project_id, trigger_id)
        if resolved is None or resolved.trigger.trigger_type not in INVOCABLE_TYPES:
            # Unknown ids and wrong-type ids get the same 404, so a caller
            # cannot learn which mechanism owns an id (§49).
            raise _not_found(project_id, trigger_id)
        context = _context(request, resolved.trigger.trigger_type, dict(body or {}))
        return ExecutionDetail.from_domain(await svc.fire(resolved, context))

    @router.get("/executions/{execution_id}")
    async def get_execution(
        execution_id: str, executions: Executions
    ) -> ExecutionDetail:
        return ExecutionDetail.from_domain(await executions.get_execution(execution_id))

    return router


def create_webhooks_router() -> APIRouter:
    """Public: webhook callers are external systems, not users.

    ponytail: anyone who knows a project id and webhook trigger id can fire
    it, the same as before accounts existed. Per-trigger webhook secrets
    belong to security hardening (Phase 25).
    """
    errors: dict[int | str, dict[str, Any]] = {
        code: {"model": ErrorResponse} for code in (404, 409, 413, 422, 500)
    }
    router = APIRouter(tags=["triggers"], responses=errors)

    @router.post(
        "/webhooks/{project_id}/{trigger_id}", status_code=status.HTTP_202_ACCEPTED
    )
    async def webhook(
        project_id: str,
        trigger_id: str,
        request: Request,
        registry: Registry,
        svc: Triggers,
    ) -> ExecutionDetail:
        resolved = await registry.resolve(
            project_id, trigger_id, trigger_type=TriggerType.WEBHOOK
        )
        if resolved is None:
            raise _not_found(project_id, trigger_id)
        raw = await request.body()
        limit = request.app.state.container.settings.webhook_max_payload_bytes
        if len(raw) > limit:
            raise PayloadTooLargeError(
                "Webhook payload exceeds the size limit.",
                details={"max_bytes": limit},
            )
        try:
            payload: Any = json.loads(raw) if raw else {}
        except (ValueError, RecursionError):
            # ValueError covers malformed JSON and bytes that are not UTF-8.
            # RecursionError is JSON nested too deeply to parse at all.
            raise DomainValidationError(
                "webhook payload must be a JSON object"
            ) from None
        if not isinstance(payload, dict):
            raise DomainValidationError("webhook payload must be a JSON object")
        context = _context(request, TriggerType.WEBHOOK, payload)
        return ExecutionDetail.from_domain(await svc.fire(resolved, context))

    return router
