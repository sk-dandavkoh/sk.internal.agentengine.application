"""FastAPI application factory and lifecycle."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from akasha.api.routes import create_api_v1_router, create_operational_router
from akasha.api.schemas import ErrorResponse, request_id
from akasha.bootstrap import Container, create_container
from akasha.config import Settings, load_settings
from akasha.errors import AkashaError
from akasha.observability.logging import configure_logging

logger = logging.getLogger("akasha")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    logger.info("application_started", extra={"event": "application_started"})
    yield
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

    register_error_handlers(app)
    return app


_HTTP_CODES = {
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    401: "AUTHENTICATION_ERROR",
    403: "AUTHORIZATION_ERROR",
    409: "CONFLICT",
}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        body = ErrorResponse(
            code=_HTTP_CODES.get(exc.status_code, "HTTP_ERROR"),
            message=str(exc.detail),
            request_id=request_id(request),
        )
        return JSONResponse(status_code=exc.status_code, content=body.model_dump())

    @app.exception_handler(AkashaError)
    async def handle_akasha_error(request: Request, exc: AkashaError) -> JSONResponse:
        body = ErrorResponse(
            code=exc.code,
            message=exc.message,
            details=exc.details,
            request_id=request_id(request),
        )
        return JSONResponse(status_code=exc.http_status, content=body.model_dump())

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        body = ErrorResponse(
            code="VALIDATION_ERROR",
            message="Request validation failed.",
            details={
                ".".join(str(p) for p in e.get("loc", ())): e.get("msg", "")
                for e in exc.errors()
            },
            request_id=request_id(request),
        )
        return JSONResponse(status_code=422, content=body.model_dump())

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # Internal trace stays server-side; the client gets a stable error.
        logger.exception(
            "unhandled_error",
            extra={"event": "unhandled_error", "request_id": request_id(request)},
            exc_info=exc,
        )
        body = ErrorResponse(
            code="INTERNAL_ERROR",
            message="An internal error occurred.",
            request_id=request_id(request),
        )
        return JSONResponse(status_code=500, content=body.model_dump())
