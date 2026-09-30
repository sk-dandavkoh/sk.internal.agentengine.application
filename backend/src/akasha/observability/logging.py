"""Structured JSON logging with context propagation and central redaction.

Correlation fields (request_id, execution_id, node_execution_id, ...) are
bound once with ``log_context`` and appear on every log line emitted inside
it — including lines from node code and libraries, because asyncio tasks
copy the context they were created in. Every line passes ``redact`` last,
so no call site has to remember to scrub secrets.
"""

import json
import logging
import sys
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from akasha.security.redaction import redact, redact_text

# Never log these record internals — everything else is context.
_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}

_context: ContextVar[Mapping[str, object]] = ContextVar("akasha_log_context")


def current_log_context() -> Mapping[str, object]:
    return _context.get({})


@contextmanager
def log_context(**fields: object) -> Iterator[None]:
    """Bind ``fields`` to every log line emitted in this (async) context."""
    token = _context.set({**current_log_context(), **fields})
    try:
        yield
    finally:
        _context.reset(token)


class JsonFormatter(logging.Formatter):
    """One JSON object per line. Precedence: fixed fields < bound context <
    the record's own ``extra`` (the most specific source wins)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **current_log_context(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED:
                payload[key] = value
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(redact(payload))


class TextFormatter(logging.Formatter):
    """Human-readable development output; redacted like the JSON output."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


def configure_logging(level: str, *, structured: bool = True) -> None:
    """Configure the root logger once at bootstrap."""
    handler = logging.StreamHandler(sys.stdout)
    if structured:
        handler.setFormatter(JsonFormatter())
    else:
        text = TextFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        text.converter = time.gmtime
        handler.setFormatter(text)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
