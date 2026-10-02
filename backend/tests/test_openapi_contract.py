"""The OpenAPI document is the frontend/backend contract (AGENTS §21).

A snapshot is committed at ``frontend/src/api/openapi.json``; the frontend's
contract test checks its runtime schemas against it. This test fails when
the snapshot no longer matches the API, so a contract change is always an
explicit, reviewed diff. Regenerate after an intended API change with:

    AKASHA_UPDATE_OPENAPI_SNAPSHOT=1 uv run pytest tests/test_openapi_contract.py
"""

import json
import os
from pathlib import Path
from typing import Any

import pytest

from akasha.api.app import create_app
from akasha.config import Settings

SNAPSHOT = Path(__file__).resolve().parents[2] / "frontend/src/api/openapi.json"


@pytest.fixture
def document(settings: Settings) -> dict[str, Any]:
    return create_app(settings=settings).openapi()


def render(document: dict[str, Any]) -> str:
    # Sorted keys: the snapshot must not change when only dict order does.
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def test_snapshot_matches_the_api(document: dict[str, Any]) -> None:
    current = render(document)
    if os.environ.get("AKASHA_UPDATE_OPENAPI_SNAPSHOT") == "1":
        SNAPSHOT.write_text(current)
    assert SNAPSHOT.read_text() == current, (
        "frontend/src/api/openapi.json is stale; regenerate it (see module docstring)"
    )


def test_every_documented_error_is_the_error_envelope(
    document: dict[str, Any],
) -> None:
    # The frontend parses every non-2xx body as ErrorResponse; the handlers in
    # api/app.py guarantee that at runtime, the document must say so too.
    wrong = [
        f"{method.upper()} {path} {status}"
        for path, operations in document["paths"].items()
        for method, operation in operations.items()
        for status, response in operation["responses"].items()
        if status[0] in "45"
        and response.get("content", {}).get("application/json", {}).get("schema")
        != {"$ref": "#/components/schemas/ErrorResponse"}
    ]
    assert wrong == []
