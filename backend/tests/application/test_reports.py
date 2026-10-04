"""Report generation units (roadmap Phase 19): parsing node-reported token
usage and cost, which is untrusted node output, and the unwired endpoint.
Persisted-state scenarios live in tests/integration/test_reports.py."""

from typing import Any

import httpx
import pytest

from akasha.api.app import create_app
from akasha.application.reports import usage_and_cost
from akasha.bootstrap import Container
from akasha.config import Settings

USAGE = {"input_tokens": 3, "output_tokens": 4}


@pytest.mark.parametrize(
    "outputs",
    [
        None,
        {},
        {"metadata": "x"},
        {"metadata": {"usage": None}},
        {"metadata": {"usage": {"input_tokens": 1}}},
        {"metadata": {"usage": {"input_tokens": True, "output_tokens": 1}}},
        {"metadata": {"usage": {"input_tokens": -1, "output_tokens": 1}}},
        {"metadata": {"usage": {"input_tokens": 1.5, "output_tokens": 1}}},
    ],
)
def test_malformed_usage_counts_as_not_reported(outputs: Any) -> None:
    assert usage_and_cost(outputs) == (None, None)


@pytest.mark.parametrize(
    "cost",
    [
        None,
        "0.1",
        {"amount": "0.1", "currency": "USD"},
        {"amount": True, "currency": "USD"},
        {"amount": -1, "currency": "USD"},
        {"amount": float("inf"), "currency": "USD"},
        {"amount": float("nan"), "currency": "USD"},
        {"amount": 1, "currency": " "},
    ],
)
def test_malformed_cost_keeps_usage(cost: Any) -> None:
    usage, parsed = usage_and_cost({"metadata": {"usage": USAGE, "cost": cost}})
    assert usage is not None and usage.total_tokens == 7
    assert parsed is None


def test_integer_cost_is_accepted() -> None:
    _, cost = usage_and_cost(
        {"metadata": {"usage": USAGE, "cost": {"amount": 2, "currency": "EUR"}}}
    )
    assert cost is not None and (cost.amount, cost.currency) == (2.0, "EUR")


async def test_report_endpoint_without_persistence() -> None:
    settings = Settings(environment="test", log_level="CRITICAL")
    app = create_app(settings=settings, container=Container(settings=settings))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.get("/api/v1/reports/executions/exec_" + "0" * 32)
    assert response.status_code == 500
    assert response.json()["code"] == "CONFIGURATION_ERROR"
