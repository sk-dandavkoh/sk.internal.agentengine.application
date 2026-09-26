"""Structured JSON logging foundation (stdlib only).

Adds `record.context` keys (event, request_id, execution_id, ...) as top-level
JSON fields so every log line is machine-parseable without a log shipper.
"""

import json
import logging
import sys
import time
from typing import Any

# Never log these record internals — everything else is context.
_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED:
                payload[key] = value
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str, *, structured: bool = True) -> None:
    """Configure the root logger once at bootstrap."""
    handler = logging.StreamHandler(sys.stdout)
    if structured:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    logging.Formatter.converter = time.gmtime  # UTC timestamps
