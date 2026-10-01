"""HTTP layer observability: one request id per request, propagated into
every log line and error body; redacted errors; the /metrics endpoint."""

import logging

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import SecretStr

from akasha.api.app import create_app
from akasha.config import Settings
from akasha.errors import AkashaError
from akasha.security.redaction import MASK
from tests.support import capture_json_logs

CANARY = "sk-canary-0123456789abcdef"


@pytest.fixture
def app_client(settings: Settings) -> TestClient:
    app = create_app(settings=settings)
    router = APIRouter()
    log = logging.getLogger("akasha.test.route")

    @router.get("/_test/logs")
    async def logs() -> dict[str, str]:
        log.info("inside handler")
        return {"ok": "yes"}

    @router.get("/_test/leaky-error")
    async def leaky() -> None:
        raise AkashaError(
            f"upstream rejected api_key={CANARY}",
            details={"password": "hunter2", "url": "https://u:hunter2@h/x"},
        )

    @router.get("/_test/boom")
    async def boom() -> None:
        raise RuntimeError(f"secret internals {CANARY}")

    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def test_caller_request_id_is_echoed_and_bound_to_request_logs(
    app_client: TestClient,
) -> None:
    with capture_json_logs() as lines, app_client:
        response = app_client.get("/_test/logs", headers={"X-Request-ID": "req-abc"})

    assert response.headers["x-request-id"] == "req-abc"
    handler_line = next(ln for ln in lines if ln["message"] == "inside handler")
    assert handler_line["request_id"] == "req-abc"
    (access,) = [ln for ln in lines if ln.get("event") == "http_request"]
    assert access["request_id"] == "req-abc"
    assert access["method"] == "GET"
    assert access["path"] == "/_test/logs"
    assert access["status_code"] == 200
    assert isinstance(access["duration_ms"], int) and access["duration_ms"] >= 0


@pytest.mark.parametrize("header", [None, "", "x" * 129, "bad\nnewline", "tab\tinside"])
def test_missing_or_unsafe_request_id_is_replaced(
    app_client: TestClient, header: str | None
) -> None:
    headers = {} if header is None else {"X-Request-ID": header}
    with app_client:
        first = app_client.get("/_test/logs", headers=headers)
        second = app_client.get("/_test/logs", headers=headers)
    rid = first.headers["x-request-id"]
    assert rid and rid != header and rid.isprintable() and len(rid) <= 128
    assert second.headers["x-request-id"] != rid  # fresh per request


def test_access_log_omits_query_string(app_client: TestClient) -> None:
    with capture_json_logs() as lines, app_client:
        app_client.get("/_test/logs?token=hunter2")
    assert "hunter2" not in str(lines)


def test_error_body_and_log_share_one_request_id(app_client: TestClient) -> None:
    with capture_json_logs() as lines, app_client:
        response = app_client.get("/_test/boom")

    body = response.json()
    assert response.status_code == 500
    assert body["request_id"] == response.headers["x-request-id"]
    (unhandled,) = [ln for ln in lines if ln.get("event") == "unhandled_error"]
    assert unhandled["request_id"] == body["request_id"]
    (access,) = [ln for ln in lines if ln.get("event") == "http_request"]
    assert access["status_code"] == 500
    assert access["request_id"] == body["request_id"]
    # The server log keeps the traceback, but redacted; the client gets none.
    assert "RuntimeError" in unhandled["exception"]
    assert CANARY not in str(lines)
    assert CANARY not in response.text and "RuntimeError" not in response.text


def test_error_messages_and_details_are_redacted(app_client: TestClient) -> None:
    with app_client:
        response = app_client.get("/_test/leaky-error")
    body = response.json()
    assert CANARY not in response.text and "hunter2" not in response.text
    assert body["message"] == f"upstream rejected api_key={MASK}"
    assert body["details"] == {"password": MASK, "url": f"https://{MASK}@h/x"}


def test_responses_are_never_cached_or_sniffed(app_client: TestClient) -> None:
    with app_client:
        responses = [app_client.get("/_test/logs"), app_client.get("/nope")]
    for response in responses:
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"


METRICS_TOKEN = SecretStr("m" * 32)


def test_metrics_endpoint_serves_prometheus_text() -> None:
    settings = Settings(
        environment="test", log_level="CRITICAL", metrics_token=METRICS_TOKEN
    )
    with TestClient(create_app(settings=settings)) as client:
        response = client.get(
            "/metrics",
            headers={"Authorization": f"Bearer {METRICS_TOKEN.get_secret_value()}"},
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    for family in (
        "akasha_executions_created_total",
        "akasha_executions_finished_total",
        "akasha_execution_duration_seconds",
        "akasha_active_executions",
        "akasha_node_execution_duration_seconds",
        "akasha_node_execution_errors_total",
    ):
        assert f"# TYPE {family.removesuffix('_total')}" in response.text
