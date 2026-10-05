"""FastAPI application factory and lifecycle."""

import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from akasha.api.routes import create_api_v1_router, create_operational_router
from akasha.api.schemas import ErrorResponse, accept_request_id, request_id
from akasha.bootstrap import Container, create_container
from akasha.config import Settings, load_settings
from akasha.errors import AkashaError
from akasha.observability.logging import log_context
from akasha.security.redaction import redact_mapping, redact_text

logger = logging.getLogger("akasha")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    await app.state.container.start()
    logger.info("application_started", extra={"event": "application_started"})
    try:
        yield
    finally:
        await app.state.container.shutdown()
        logger.info("application_stopped", extra={"event": "application_stopped"})


def create_app(
    settings: Settings | None = None, container: Container | None = None
) -> FastAPI:
    # Logging is the process's (akasha.main), never the factory's: an
    # in-process caller such as a test keeps its own handlers.
    settings = settings or load_settings()
    app = FastAPI(title="akasha-engine", version="0.1.0", lifespan=lifespan)
    app.state.container = container or create_container(settings)

    app.include_router(create_operational_router())
    app.include_router(create_api_v1_router())

    # Innermost (added first): BaseHTTPMiddleware would wrap its 413 into an
    # ExceptionGroup, and the access log should record the rejection.
    app.add_middleware(RequestSizeLimit, max_bytes=settings.api_max_request_bytes)
    register_request_observability(app)
    register_error_handlers(app)
    return app


class RequestSizeLimit:
    """Caps every request body (architecture §107), counted as it streams
    in, so no route can be made to buffer an unbounded body. Routes with a
    smaller limit (webhooks, packages) enforce theirs on top."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        received = 0

        async def limited() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # HTTPException: FastAPI re-raises it from body parsing
                    # (any other exception there becomes a generic 400).
                    raise HTTPException(
                        413, f"The request body exceeds {self.max_bytes} bytes."
                    )
            return message

        await self.app(scope, limited, send)


def register_request_observability(app: FastAPI) -> None:
    @app.middleware("http")
    async def observe_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = accept_request_id(request.headers.get("x-request-id"))
        request.state.request_id = rid
        start = time.perf_counter()
        status = 500  # unless a response says otherwise (unhandled error)
        with log_context(request_id=rid):
            try:
                response = await call_next(request)
                status = response.status_code
            finally:
                logger.info(
                    "http_request",
                    extra={
                        "event": "http_request",
                        "method": request.method,
                        # Path only: query strings can carry tokens.
                        "path": request.url.path,
                        "status_code": status,
                        "duration_ms": round((time.perf_counter() - start) * 1000),
                        "user_id": getattr(request.state, "user_id", None),
                    },
                )
        response.headers["X-Request-ID"] = rid
        # API bodies carry session tokens and workflow data: never cached by
        # browsers or proxies, never content-sniffed into something active.
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response


def _error(
    request: Request,
    status: int,
    code: str,
    message: str,
    details: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Every error body leaves through here, redacted: messages and details
    can echo caller or node input."""
    body = ErrorResponse(
        code=code,
        message=redact_text(message),
        details=redact_mapping(details or {}),
        request_id=request_id(request),
    )
    return JSONResponse(
        status_code=status,
        content=body.model_dump(),
        headers={**(headers or {}), "X-Request-ID": body.request_id},
    )


_HTTP_CODES = {
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    401: "AUTHENTICATION_ERROR",
    403: "AUTHORIZATION_ERROR",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return _error(
            request,
            exc.status_code,
            _HTTP_CODES.get(exc.status_code, "HTTP_ERROR"),
            str(exc.detail),
        )

    @app.exception_handler(AkashaError)
    async def handle_akasha_error(request: Request, exc: AkashaError) -> JSONResponse:
        headers = {}
        if exc.http_status == 401:
            headers["WWW-Authenticate"] = "Bearer"  # RFC 6750
        if "retry_after_seconds" in exc.details:
            headers["Retry-After"] = str(exc.details["retry_after_seconds"])
        return _error(
            request, exc.http_status, exc.code, exc.message, exc.details, headers
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _error(
            request,
            422,
            "VALIDATION_ERROR",
            "Request validation failed.",
            {
                ".".join(str(p) for p in e.get("loc", ())): e.get("msg", "")
                for e in exc.errors()
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # Internal trace stays server-side; the client gets a stable error.
        logger.exception(
            "unhandled_error",
            extra={"event": "unhandled_error", "request_id": request_id(request)},
            exc_info=exc,
        )
        return _error(request, 500, "INTERNAL_ERROR", "An internal error occurred.")
