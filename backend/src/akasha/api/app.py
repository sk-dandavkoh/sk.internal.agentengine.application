"""FastAPI application factory and lifecycle."""

import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from akasha.api.routes import create_api_v1_router, create_operational_router
from akasha.api.schemas import ErrorResponse, accept_request_id, request_id
from akasha.bootstrap import Container, create_container
from akasha.config import Settings, load_settings
from akasha.errors import AkashaError
from akasha.observability.logging import configure_logging, log_context
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
    settings = settings or load_settings()
    # In tests, pytest owns the logging handlers; the app must not evict them.
    if settings.environment != "test":
        configure_logging(settings.log_level)
    app = FastAPI(title="akasha-engine", version="0.1.0", lifespan=lifespan)
    app.state.container = container or create_container(settings)

    app.include_router(create_operational_router())
    app.include_router(create_api_v1_router())

    register_request_observability(app)
    register_error_handlers(app)
    return app


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
