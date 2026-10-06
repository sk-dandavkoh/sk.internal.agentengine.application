"""Error-handling foundation: structured responses, stable codes, no traces."""

from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi.testclient import TestClient

from akasha.api.app import create_app
from akasha.api.auth import current_identity
from akasha.application.persistence import UnitOfWork
from akasha.application.workflows import WorkflowService
from akasha.bootstrap import Container
from akasha.config import Settings
from akasha.domain.identity import Identity, Role, User, UserSession
from akasha.errors import AkashaError

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_USER = User.create("admin", role=Role.ADMIN)
ADMIN = Identity(_USER, UserSession("hash", _USER.id, _NOW, _NOW))


def _app_with_broken_route() -> TestClient:
    app = create_app(settings=Settings(environment="test", log_level="CRITICAL"))
    router = APIRouter()

    @router.get("/_test/domain-error")
    async def domain_error() -> None:
        raise AkashaError("Workflow not found.", details={"workflow_id": "p_1"})

    @router.get("/_test/boom")
    async def boom() -> None:
        raise RuntimeError("secret internals")

    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def test_akasha_error_mapped_to_structured_response() -> None:
    with _app_with_broken_route() as client:
        response = client.get("/_test/domain-error")
    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "INTERNAL_ERROR"
    assert body["message"] == "Workflow not found."
    assert body["details"] == {"workflow_id": "p_1"}
    assert body["request_id"]


def test_akasha_error_has_stable_code() -> None:
    class GoneError(AkashaError):
        code = "NOT_FOUND"
        http_status = 404

    assert GoneError("x").http_status == 404


def test_unexpected_error_does_not_leak_traceback() -> None:
    with _app_with_broken_route() as client:
        response = client.get("/_test/boom")
    body = response.json()
    assert body["code"] == "INTERNAL_ERROR"
    assert body["message"] == "An internal error occurred."
    assert "secret internals" not in response.text
    assert "RuntimeError" not in response.text


def test_404_is_structured(client: TestClient) -> None:
    response = client.get("/no-such-route")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "NOT_FOUND"
    assert body["request_id"]


def test_request_validation_error_is_structured(settings: Settings) -> None:
    """Invalid bodies are rejected at the boundary: structured 422, request
    id echoed, and the service / persistence is never reached."""

    def no_persistence() -> UnitOfWork:
        raise AssertionError("invalid requests must not reach persistence")

    container = Container(
        settings=settings, workflows=WorkflowService(no_persistence, {})
    )
    app = create_app(settings=settings, container=container)
    app.dependency_overrides[current_identity] = lambda: ADMIN
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/workflows", json={"name": ""}, headers={"x-request-id": "req_42"}
        )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["request_id"] == "req_42"
    assert "body.name" in body["details"]


def test_workflow_api_without_persistence_is_a_configuration_error(
    client: TestClient,
) -> None:
    """Without the database there is no authentication either: the API
    fails closed with CONFIGURATION_ERROR, never open."""
    response = client.get("/api/v1/workflows")
    assert response.status_code == 500
    assert response.json()["code"] == "CONFIGURATION_ERROR"
