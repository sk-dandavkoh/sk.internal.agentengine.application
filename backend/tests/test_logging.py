"""Structured logging: JSON schema, bound correlation context, redaction."""

import asyncio
import io
import json
import logging
import sys
from datetime import datetime, timedelta

import pytest

from akasha.observability.logging import (
    JsonFormatter,
    TextFormatter,
    configure_logging,
    current_log_context,
    log_context,
)
from akasha.security.redaction import MASK
from tests.support import capture_json_logs

log = logging.getLogger("akasha.test")


def test_json_line_schema() -> None:
    with capture_json_logs() as lines:
        log.info("node failed", extra={"event": "node_failed", "duration_ms": 12})

    (line,) = lines
    assert set(line) >= {"timestamp", "level", "logger", "message", "event"}
    assert line["level"] == "INFO"
    assert line["logger"] == "akasha.test"
    assert line["message"] == "node failed"
    assert line["event"] == "node_failed"
    assert line["duration_ms"] == 12
    # ISO 8601, UTC, millisecond precision.
    stamp = datetime.fromisoformat(line["timestamp"])
    assert stamp.utcoffset() == timedelta(0)
    assert line["timestamp"].endswith("+00:00")


def test_bound_context_appears_on_every_line_and_is_restored() -> None:
    with capture_json_logs() as lines:
        with log_context(request_id="req-1", execution_id="exec-1"):
            log.info("outer")
            with log_context(node_id="n-1"):
                log.info("inner")
            log.info("after inner")
        log.info("outside")

    assert [ln.get("request_id") for ln in lines] == ["req-1"] * 3 + [None]
    assert [ln.get("node_id") for ln in lines] == [None, "n-1", None, None]
    assert current_log_context() == {}


def test_record_extra_wins_over_bound_context() -> None:
    with capture_json_logs() as lines, log_context(node_id="bound"):
        log.info("x", extra={"node_id": "explicit"})
    assert lines[0]["node_id"] == "explicit"


async def test_context_propagates_into_tasks_and_does_not_leak_between_them() -> None:
    async def work(label: str) -> None:
        await asyncio.sleep(0)  # interleave the two tasks
        log.info(label)

    with capture_json_logs() as lines:
        with log_context(execution_id="exec-a"):
            first = asyncio.create_task(work("a"))
        with log_context(execution_id="exec-b"):
            second = asyncio.create_task(work("b"))
        await asyncio.gather(first, second)

    assert {ln["message"]: ln["execution_id"] for ln in lines} == {
        "a": "exec-a",
        "b": "exec-b",
    }


def test_secrets_are_redacted_from_message_fields_context_and_exception() -> None:
    canary = "sk-canary-0123456789abcdef"
    with capture_json_logs() as lines, log_context(authorization=f"Bearer {canary}"):
        try:
            raise ValueError(f"connect failed api_key={canary}")
        except ValueError:
            log.exception(
                "calling https://bob:%s@api.example/x",
                "hunter2pass",
                extra={
                    "headers": {"X-Api-Key": canary},
                    "note": f"used {canary}",
                },
            )

    raw = json.dumps(lines)
    assert canary not in raw
    assert "hunter2pass" not in raw
    (line,) = lines
    assert line["authorization"] == MASK
    assert line["headers"] == {"X-Api-Key": MASK}
    assert line["message"] == f"calling https://{MASK}@api.example/x"
    assert "ValueError" in line["exception"]


def test_json_formatter_renders_unknown_objects_safely() -> None:
    class Opaque:
        def __str__(self) -> str:
            return "token=abc123"

    record = logging.makeLogRecord({"msg": "m", "thing": Opaque()})
    assert json.loads(JsonFormatter().format(record))["thing"] == f"token={MASK}"


def test_text_formatter_redacts() -> None:
    record = logging.makeLogRecord({"msg": "password=hunter2"})
    assert "hunter2" not in TextFormatter("%(message)s").format(record)


@pytest.mark.parametrize("structured", [True, False])
def test_configure_logging_writes_redacted_lines_to_stdout(
    monkeypatch: pytest.MonkeyPatch, structured: bool
) -> None:
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    try:
        configure_logging("INFO", structured=structured)
        log.info("password=hunter2")
    finally:
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])
    assert "hunter2" not in out.getvalue()
    assert MASK in out.getvalue()
    if structured:
        assert json.loads(out.getvalue())["level"] == "INFO"
