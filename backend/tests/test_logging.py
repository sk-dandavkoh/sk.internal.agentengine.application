"""Structured logging: JSON output carries context fields."""

import json
import logging

from akasha.observability.logging import JsonFormatter


def test_json_formatter_includes_context_and_exception() -> None:
    record = logging.getLogger("test").makeRecord(
        name="akasha",
        level=logging.INFO,
        fn="f.py",
        lno=1,
        msg="node failed",
        args=(),
        exc_info=None,
        extra={"event": "node_failed", "execution_id": "ex_123", "duration_ms": 12},
    )
    payload = json.loads(JsonFormatter().format(record))
    assert payload["level"] == "INFO"
    assert payload["logger"] == "akasha"
    assert payload["message"] == "node failed"
    assert payload["event"] == "node_failed"
    assert payload["execution_id"] == "ex_123"
    assert payload["duration_ms"] == 12
