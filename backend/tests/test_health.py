"""Health and readiness endpoints (§67 backend/AGENTS.md)."""

from fastapi.testclient import TestClient

from akasha import __version__


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_ready_with_no_checks(client: TestClient) -> None:
    # Readiness = dependencies usable; an engine with none is ready.
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "failures": {}}


def test_ready_reports_failing_checks(client: TestClient) -> None:
    async def failing_check() -> str | None:
        return "cannot connect"

    client.app.state.container.ready_checks["database"] = failing_check  # type: ignore[attr-defined]
    response = client.get("/ready")
    assert response.status_code == 503
    expected = {"status": "not_ready", "failures": {"database": "cannot connect"}}
    assert response.json() == expected


def test_operational_endpoints_not_under_api_v1(client: TestClient) -> None:
    assert client.get("/api/v1/health").status_code == 404
