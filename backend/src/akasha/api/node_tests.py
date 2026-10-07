"""POST /workflows/{workflow_id}/nodes/{node_id}/test — run one node of the
saved draft alone (ADR 0010). Editors only (the default for a write under
``{workflow_id}``): a test uses the node's credentials and has its side
effects, like a run the editor could start by publishing.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, JsonValue

from akasha.api.schemas import ErrorResponse
from akasha.domain.execution import LogLevel
from akasha.errors import ConfigurationError
from akasha.runtime.node_test import NodeTester, NodeTestResult


def _tester(request: Request) -> NodeTester:
    tester: NodeTester | None = request.app.state.container.node_tester
    if tester is None:
        raise ConfigurationError("Workflow persistence is not configured.")
    return tester


Tester = Annotated[NodeTester, Depends(_tester)]


class NodeTestRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    # Port name to value; ports left out are unconnected (their default).
    inputs: dict[str, JsonValue] = Field(default_factory=dict)
    # What core.input reads: the run input of a real execution.
    execution_input: dict[str, JsonValue] = Field(default_factory=dict)


class NodeTestError(BaseModel):
    code: str
    message: str
    kind: str
    retryable: bool
    details: dict[str, str]


class NodeTestLog(BaseModel):
    level: LogLevel
    message: str
    logged_at: datetime
    fields: dict[str, JsonValue]


class NodeTestResponse(BaseModel):
    status: Literal["completed", "failed"]
    duration_ms: int
    outputs: dict[str, JsonValue] | None
    error: NodeTestError | None
    logs: list[NodeTestLog]

    @classmethod
    def from_result(cls, result: NodeTestResult) -> "NodeTestResponse":
        error = result.error
        return cls(
            status="completed" if result.status == "completed" else "failed",
            duration_ms=result.duration_ms,
            outputs=result.outputs,
            error=None
            if error is None
            else NodeTestError(
                code=error.code,
                message=error.message,
                kind=error.kind.value,
                retryable=error.retryable,
                details={k: str(v) for k, v in error.details.items()},
            ),
            logs=[
                NodeTestLog(
                    level=level,
                    message=message,
                    logged_at=at,
                    fields=_fields(fields),
                )
                for level, message, at, fields in result.logs
            ],
        )


def _fields(fields: Any) -> dict[str, JsonValue]:
    # Already redacted and storable (NodeLogCapture); a plain copy.
    return dict(fields)


def create_node_tests_router() -> APIRouter:
    errors: dict[int | str, dict[str, Any]] = {
        code: {"model": ErrorResponse} for code in (404, 409, 422, 500)
    }
    router = APIRouter(prefix="/workflows", tags=["workflows"], responses=errors)

    @router.post("/{workflow_id}/nodes/{node_id}/test")
    async def test_node(
        workflow_id: str, node_id: str, body: NodeTestRequest, tester: Tester
    ) -> NodeTestResponse:
        """Runs the node once with ``inputs``; nothing is persisted. A node
        failure is a result (``status: failed``), not an error response."""
        result = await tester.test(
            workflow_id,
            node_id,
            expected_revision=body.expected_revision,
            inputs=body.inputs,
            execution_input=body.execution_input,
        )
        return NodeTestResponse.from_result(result)

    return router
