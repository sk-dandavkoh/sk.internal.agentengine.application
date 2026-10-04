"""Capture what a node attempt logs, for the execution's log history.

Node code logs through ``context.logger`` (``akasha.nodes.<type>``). One
handler on ``akasha.nodes`` hands every record to the capture of the attempt
whose task emitted it: the capture lives in a ``ContextVar`` set when the
task is created, so concurrent attempts never see each other's lines, and
records from outside any attempt are ignored. The process log still gets
every line through the root handler as before.

Each line is redacted here, inside the node's task, so values the node
resolved from the credential store are masked exactly (``redact`` reads the
task's known secrets), on top of the pattern rules. Records below the
configured log level never reach a handler, so they are not captured either.

Bounded per attempt (AGENTS §43/§76): a line count, a message length and a
size for the structured fields. Lines over the count are counted, not kept,
and a closing WARNING line says how many were dropped.
"""

import logging
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Any, Final, cast

from akasha.domain.execution import LogLevel
from akasha.domain.limits import json_size
from akasha.domain.primitives import is_storable
from akasha.observability.logging import record_extras
from akasha.security.redaction import redact, redact_text

MAX_LINES_PER_ATTEMPT: Final = 200
MAX_MESSAGE_CHARS: Final = 4_000
MAX_FIELDS_BYTES: Final = 8 * 1024
# Correlation ids are columns of the stored line or the execution already.
_IMPLIED_FIELDS: Final = frozenset({"execution_id", "node_execution_id", "node_id"})

type LogLine = tuple[LogLevel, str, datetime, Mapping[str, Any]]


class NodeLogCapture:
    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self._lines: list[LogLine] = []
        self.dropped = 0

    def add(self, record: logging.LogRecord) -> None:
        if len(self._lines) >= MAX_LINES_PER_ATTEMPT:
            self.dropped += 1
            return
        message = redact_text(record.getMessage())
        if len(message) > MAX_MESSAGE_CHARS:
            message = message[:MAX_MESSAGE_CHARS] + " […truncated]"
        fields = {
            k: v for k, v in record_extras(record).items() if k not in _IMPLIED_FIELDS
        }
        if record.exc_info and record.exc_info[0] is not None:
            # The type only: exception text and tracebacks are arbitrary
            # text and server paths (AGENTS §8); the process log keeps them.
            fields["exception"] = record.exc_info[0].__name__
        safe = cast("dict[str, Any]", redact(fields))
        if not is_storable(safe) or json_size(safe) > MAX_FIELDS_BYTES:
            safe = {"fields_omitted": f"over {MAX_FIELDS_BYTES} bytes or not JSON"}
        self._lines.append((_level(record.levelno), message, self._clock(), safe))

    def lines(self) -> list[LogLine]:
        if not self.dropped:
            return list(self._lines)
        note = (
            f"{self.dropped} further log lines of this attempt were dropped"
            f" (limit {MAX_LINES_PER_ATTEMPT})"
        )
        return [*self._lines, (LogLevel.WARNING, note, self._clock(), {})]


_THRESHOLDS: Final = (
    (logging.CRITICAL, LogLevel.CRITICAL),
    (logging.ERROR, LogLevel.ERROR),
    (logging.WARNING, LogLevel.WARNING),
    (logging.INFO, LogLevel.INFO),
)


def _level(levelno: int) -> LogLevel:
    """Custom levels round down to the standard one below them."""
    return next((lv for n, lv in _THRESHOLDS if levelno >= n), LogLevel.DEBUG)


_current: ContextVar[NodeLogCapture | None] = ContextVar(
    "akasha_node_log_capture", default=None
)


@contextmanager
def capturing(capture: NodeLogCapture) -> Iterator[None]:
    """Route node log records emitted in this context (and tasks created in
    it) to ``capture``."""
    token = _current.set(capture)
    try:
        yield
    finally:
        _current.reset(token)


class _CaptureHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        capture = _current.get()
        if capture is None:
            return
        try:
            capture.add(record)
        except Exception:
            # A malformed record (bad format args, unencodable extras) must
            # not fail the node; logging's own policy reports it to stderr.
            self.handleError(record)


# Installed once on import; configure_logging only resets root handlers.
logging.getLogger("akasha.nodes").addHandler(_CaptureHandler())
