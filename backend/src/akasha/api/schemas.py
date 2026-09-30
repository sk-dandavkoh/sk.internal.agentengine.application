"""API response schemas (boundary models, not domain models)."""

import uuid
from typing import Any

from fastapi import Request
from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str


def accept_request_id(header: str | None) -> str:
    """The caller's ``X-Request-ID`` if it is log-safe (1-128 printable
    chars, the same rule as execution correlation ids), else a fresh one."""
    if header and len(header) <= 128 and header.isprintable():
        return header
    return str(uuid.uuid4())


def request_id(request: Request) -> str:
    """The id the request middleware assigned (one id per request)."""
    rid: str | None = getattr(request.state, "request_id", None)
    return rid or accept_request_id(request.headers.get("x-request-id"))
