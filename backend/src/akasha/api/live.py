"""Live execution events over WebSocket (architecture §23).

``WS /api/v1/executions/{execution_id}/live``, JSON text frames:

- client → ``{"type": "subscribe", "token": "<session token>", "after": 0}``,
  the first frame, within ``SUBSCRIBE_TIMEOUT_SECONDS``. Browsers cannot set
  headers on a WebSocket, and the token must not ride in the URL (query
  strings reach logs), so it travels in this frame. ``after`` is the last
  event position the client has seen.
- server → ``{"type": "subscribed", "execution_id": ..., "after": n}``
- server → ``{"type": "events", "items": [EventOut, ...]}``, ascending by
  position, every stored event with ``position > after`` exactly once per
  connection.
- server → ``{"type": "end", "status": "<terminal status>"}``, then close
  1000: the execution is terminal and every one of its events was sent.

The stream is a tail of the persisted event log, read from PostgreSQL, not
a second source of truth: it sees events of every process (API, workers)
and a reconnect with ``after`` resumes where the connection dropped.
Clients still refetch ``GET /executions/{id}`` after (re)subscribing: the
events say what happened, the execution resource is the state.

Close codes: 4400 malformed or missing subscribe frame, 4401 invalid or
expired session (also when it is revoked mid-stream), 4404 unknown or not
visible execution (one answer for both, like HTTP 404), 1011 internal or
configuration error. Any other close is transient: reconnect.

The web UI has no token (ADR 0002): it subscribes with an empty token and
the handshake's session cookie counts instead, but only when the handshake
comes from a page on this origin (``Origin`` matches ``Host`` or
``X-Forwarded-Host``). A WebSocket has no CORS and cannot carry the
anti-CSRF header, so the Origin check is what keeps another page on the
same site from riding on the cookie.
"""

import asyncio
import logging
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from akasha.api.auth import SESSION_COOKIE
from akasha.api.executions import EventOut
from akasha.application.auth import AuthenticationRequiredError
from akasha.application.executions import ExecutionNotFoundError
from akasha.application.projects import MAX_PAGE_SIZE
from akasha.bootstrap import Container

logger = logging.getLogger("akasha.api.live")

SUBSCRIBE_TIMEOUT_SECONDS = 10.0
_MAX_TOKEN_LENGTH = 256

CLOSE_PROTOCOL = 4400
CLOSE_UNAUTHENTICATED = 4401
CLOSE_NOT_FOUND = 4404
CLOSE_INTERNAL = 1011


class SubscribeMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["subscribe"]
    # Missing or empty: unauthenticated (4401), like HTTP without a header.
    token: str = Field(default="", max_length=_MAX_TOKEN_LENGTH)
    after: int = Field(default=0, ge=0)


class _Closed(Exception):
    """Ends the stream with a close code (not an error: a protocol outcome)."""

    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


def create_live_router() -> APIRouter:
    """Mounted outside the HTTP-guarded router: the guards read headers a
    WebSocket cannot send; the subscribe frame authenticates instead."""
    router = APIRouter()

    @router.websocket("/executions/{execution_id}/live")
    async def live_execution(websocket: WebSocket, execution_id: str) -> None:
        await websocket.accept()
        log = {"execution_id": execution_id}
        try:
            await _stream(websocket, websocket.app.state.container, execution_id)
        except WebSocketDisconnect:
            logger.info(
                "live_disconnected", extra={"event": "live_disconnected", **log}
            )
        except _Closed as closed:
            logger.info(
                "live_closed",
                extra={"event": "live_closed", "close_code": closed.code, **log},
            )
            await _close(websocket, closed.code, closed.reason)
        # Deliberately broad: a bug must close this one connection with a
        # stable code, never leak a trace to the client.
        except Exception:
            logger.exception("live_failed", extra={"event": "live_failed", **log})
            await _close(websocket, CLOSE_INTERNAL, "Internal error.")

    return router


async def _close(websocket: WebSocket, code: int, reason: str) -> None:
    """Close unless either side already has: a second close is an error of
    its own and would bury the first in the logs."""
    if (
        websocket.client_state == WebSocketState.CONNECTED
        and websocket.application_state == WebSocketState.CONNECTED
    ):
        await websocket.close(code, reason)


async def _receive_text(websocket: WebSocket) -> str:
    message = await websocket.receive()
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code", 1000))
    text = message.get("text")
    if not isinstance(text, str):
        raise _Closed(CLOSE_PROTOCOL, "Expected a JSON text frame.")
    return text


async def _stream(websocket: WebSocket, c: Container, execution_id: str) -> None:
    if c.auth is None or c.authorization is None or c.executions is None:
        raise _Closed(CLOSE_INTERNAL, "Live updates are not configured.")
    try:
        async with asyncio.timeout(SUBSCRIBE_TIMEOUT_SECONDS):
            subscribe = SubscribeMessage.model_validate_json(
                await _receive_text(websocket)
            )
    except (TimeoutError, ValidationError) as exc:
        raise _Closed(CLOSE_PROTOCOL, "Expected a subscribe frame.") from exc
    token = subscribe.token or _cookie_token(websocket)
    try:
        if not token:
            raise AuthenticationRequiredError("Authentication required.")
        identity = await c.auth.authenticate(token)
        await c.authorization.require_execution(identity, execution_id)
    except AuthenticationRequiredError as exc:
        raise _Closed(CLOSE_UNAUTHENTICATED, "Authentication required.") from exc
    except ExecutionNotFoundError as exc:
        raise _Closed(CLOSE_NOT_FOUND, "Execution not found.") from exc

    logger.info(
        "live_subscribed",
        extra={
            "event": "live_subscribed",
            "execution_id": execution_id,
            "user_id": identity.user.id,
        },
    )
    after = subscribe.after
    await websocket.send_json(
        {"type": "subscribed", "execution_id": execution_id, "after": after}
    )
    # ponytail: polls PostgreSQL per subscription (3 small indexed queries
    # per tick). Wake on LISTEN/NOTIFY or Redis pub/sub if viewers grow.
    while True:
        execution, page = await c.executions.follow_events(
            execution_id, after=after, limit=MAX_PAGE_SIZE
        )
        if page.items:
            items = [
                EventOut.from_domain(e).model_dump(mode="json") for e in page.items
            ]
            await websocket.send_json({"type": "events", "items": items})
            after = items[-1]["position"]
        if page.has_more:
            continue
        if execution.is_terminal:
            await websocket.send_json({"type": "end", "status": execution.status})
            await websocket.close(1000)
            return
        # Waiting on the socket instead of sleeping notices a client that
        # left; the client never needs to send anything after subscribing.
        try:
            async with asyncio.timeout(c.settings.live_poll_seconds):
                await _receive_text(websocket)
            raise _Closed(CLOSE_PROTOCOL, "No frames expected after subscribe.")
        except TimeoutError:
            pass
        try:
            await c.auth.authenticate(token)
        except AuthenticationRequiredError as exc:
            raise _Closed(CLOSE_UNAUTHENTICATED, "Session expired.") from exc


def _cookie_token(websocket: WebSocket) -> str:
    origin = websocket.headers.get("origin")
    hosts = {
        websocket.headers.get("host"),
        websocket.headers.get("x-forwarded-host"),
    } - {None}
    if origin is None or urlsplit(origin).netloc not in hosts:
        return ""
    token = websocket.cookies.get(SESSION_COOKIE, "")
    return token if len(token) <= _MAX_TOKEN_LENGTH else ""
