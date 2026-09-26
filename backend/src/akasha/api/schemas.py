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


def request_id(request: Request) -> str:
    rid = request.headers.get("x-request-id")
    return rid if rid else str(uuid.uuid4())
