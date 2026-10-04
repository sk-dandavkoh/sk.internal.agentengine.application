"""Node log capture (frontend Phase 15): routing, bounds and redaction."""

import asyncio
import logging
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from akasha.domain.execution import LogLevel, NodeLogEntry
from akasha.errors import DomainValidationError
from akasha.runtime.node_logs import (
    MAX_FIELDS_BYTES,
    MAX_LINES_PER_ATTEMPT,
    MAX_MESSAGE_CHARS,
    NodeLogCapture,
    capturing,
)
from akasha.security.redaction import register_secret

T0 = datetime(2100, 1, 1, tzinfo=UTC)
EXEC = "exec_" + "1" * 32
NODE = "node_" + "2" * 32
log = logging.getLogger("akasha.nodes.test.capture")


@pytest.fixture(autouse=True)
def _debug_level() -> Iterator[None]:
    log.setLevel(logging.DEBUG)  # setLevel also clears the enabled-cache
    yield
    log.setLevel(logging.NOTSET)


def capture() -> NodeLogCapture:
    return NodeLogCapture(lambda: T0)


def test_lines_of_the_attempt_are_captured_with_level_and_fields() -> None:
    c = capture()
    with capturing(c):
        log.info("fetched %d rows", 3, extra={"rows": 3, "node_id": "n1"})
        log.log(25, "between info and warning")
        log.error("boom")
    assert c.lines() == [
        # node_id is implied by the stored line, not repeated as a field.
        (LogLevel.INFO, "fetched 3 rows", T0, {"rows": 3}),
        (LogLevel.INFO, "between info and warning", T0, {}),
        (LogLevel.ERROR, "boom", T0, {}),
    ]


def test_nothing_is_captured_outside_an_attempt() -> None:
    c = capture()
    log.info("not in an attempt")
    with capturing(c):
        logging.getLogger("akasha.runtime").info("not a node logger")
    assert c.lines() == []


async def test_concurrent_attempts_never_see_each_others_lines() -> None:
    a, b = capture(), capture()

    async def node(name: str) -> None:
        for i in range(3):
            log.info("%s %d", name, i)
            await asyncio.sleep(0)

    with capturing(a):
        task_a = asyncio.create_task(node("a"))
    with capturing(b):
        task_b = asyncio.create_task(node("b"))
    await asyncio.gather(task_a, task_b)
    assert [m for _, m, _, _ in a.lines()] == ["a 0", "a 1", "a 2"]
    assert [m for _, m, _, _ in b.lines()] == ["b 0", "b 1", "b 2"]


def test_lines_over_the_limit_are_counted_and_reported() -> None:
    c = capture()
    with capturing(c):
        for i in range(MAX_LINES_PER_ATTEMPT + 5):
            log.info("line %d", i)
    lines = c.lines()
    assert len(lines) == MAX_LINES_PER_ATTEMPT + 1
    assert lines[-2][1] == f"line {MAX_LINES_PER_ATTEMPT - 1}"
    level, note, _, _ = lines[-1]
    assert level is LogLevel.WARNING
    assert note.startswith("5 further log lines")


def test_long_messages_and_large_fields_are_bounded() -> None:
    c = capture()
    with capturing(c):
        log.info("x" * (MAX_MESSAGE_CHARS + 100))
        log.info("big", extra={"blob": "y" * MAX_FIELDS_BYTES})
        log.info("odd", extra={"ratio": float("nan")})
    (_, long, _, _), (_, _, _, big), (_, _, _, odd) = c.lines()
    assert len(long) < MAX_MESSAGE_CHARS + 20 and long.endswith("[…truncated]")
    assert set(big) == {"fields_omitted"}
    assert set(odd) == {"fields_omitted"}


def test_secrets_are_redacted_before_they_are_kept() -> None:
    c = capture()

    async def node() -> None:
        register_secret("plain-shapeless-value")  # resolved from the store
        log.info(
            "calling with sk-live-0123456789abcdefghij and plain-shapeless-value",
            extra={"api_key": "anything", "note": "plain-shapeless-value"},
        )

    with capturing(c):
        asyncio.run(node())
    ((_, message, _, fields),) = c.lines()
    assert "sk-live-0123456789abcdefghij" not in message
    assert "plain-shapeless-value" not in message
    assert fields == {"api_key": "[REDACTED]", "note": "[REDACTED]"}


def test_exceptions_keep_their_type_only() -> None:
    c = capture()
    with capturing(c):
        try:
            raise ValueError("secret detail /srv/app/path.py")
        except ValueError:
            log.exception("failed")
    ((level, message, _, fields),) = c.lines()
    assert (level, message, fields) == (
        LogLevel.ERROR,
        "failed",
        {"exception": "ValueError"},
    )


def test_a_malformed_record_does_not_break_the_node(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Only the capture handler: pytest's own root handler re-raises
    # formatting errors, production handlers report them to stderr.
    monkeypatch.setattr(logging.getLogger("akasha.nodes"), "propagate", False)
    monkeypatch.setattr(logging, "raiseExceptions", False)
    c = capture()
    with capturing(c):
        log.info("%d rows", "not a number")  # bad format args
        log.info("still logging")
    assert [m for _, m, _, _ in c.lines()] == ["still logging"]


def test_log_level_order() -> None:
    assert LogLevel.WARNING.at_least() == (
        LogLevel.WARNING,
        LogLevel.ERROR,
        LogLevel.CRITICAL,
    )
    assert LogLevel.DEBUG.at_least() == tuple(LogLevel)


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"attempt": 0}, "attempt"),
        ({"logged_at": datetime(2100, 1, 1)}, "timezone"),
        ({"fields": {"x": float("inf")}}, "storable"),
    ],
)
def test_log_entry_rejects_invalid_values(
    change: dict[str, object], problem: str
) -> None:
    valid: dict[str, object] = {
        "execution_id": EXEC,
        "node_id": NODE,
        "attempt": 1,
        "level": "INFO",
        "message": "m",
        "logged_at": T0,
    }
    with pytest.raises(DomainValidationError, match=problem):
        NodeLogEntry(**{**valid, **change})  # type: ignore[arg-type]


def test_log_entry_replaces_unstorable_characters() -> None:
    entry = NodeLogEntry(EXEC, NODE, 1, LogLevel.INFO, "a\x00b", T0)
    assert entry.message == "a�b"
