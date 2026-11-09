"""Application entry point and FastAPI initialization factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import api_v1_router
from app.core.config import get_settings
from app.core.exceptions import AppException
from app.core.logging import setup_logging
from app.db.session import close_database_connections
from app.schemas.health import WelcomeResponse

logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Handle application startup and shutdown lifecycle events."""
    settings = get_settings()
    setup_logging(settings)
    logger.info(
        "Starting %s in %s mode (v%s)",
        settings.app_name,
        settings.environment,
        settings.app_version,
    )
    yield
    logger.info("Shutting down %s", settings.app_name)
    await close_database_connections()


def create_application() -> FastAPI:
    """Instantiate and configure the FastAPI application."""
    settings = get_settings()

    application = FastAPI(
        title=settings.app_name,
        description=(
            "Distributed AI Agent Orchestration System coordinating specialized "
            "agents via FastAPI, LangGraph, Gemini API, Redis, PostgreSQL, and Celery."
        ),
        version=settings.app_version,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # Middleware: CORS
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Exception Handlers
    @application.exception_handler(AppException)
    async def app_exception_handler(_: Request, exc: AppException) -> JSONResponse:
        logger.error("Domain exception: %s (code=%s)", exc.message, exc.code)
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        if exc.code in ("RESOURCE_NOT_FOUND", "AGENT_NOT_FOUND"):
            status_code = status.HTTP_404_NOT_FOUND
        elif exc.code == "AGENT_VALIDATION_ERROR":
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        elif exc.code == "AGENT_REGISTRATION_ERROR":
            status_code = status.HTTP_409_CONFLICT
        elif exc.code in ("LLM_INVALID_REQUEST_ERROR", "TOOL_REGISTRATION_ERROR"):
            status_code = status.HTTP_400_BAD_REQUEST
        elif exc.code == "LLM_AUTHENTICATION_ERROR":
            status_code = status.HTTP_401_UNAUTHORIZED
        elif exc.code == "LLM_RATE_LIMIT_ERROR":
            status_code = status.HTTP_429_TOO_MANY_REQUESTS
        elif exc.code == "LLM_TIMEOUT_ERROR":
            status_code = status.HTTP_504_GATEWAY_TIMEOUT
        elif exc.code in ("LLM_STRUCTURED_OUTPUT_ERROR", "LLM_ERROR"):
            status_code = status.HTTP_502_BAD_GATEWAY
        elif exc.code == "AGENT_EXECUTION_ERROR":
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR

        return JSONResponse(
            status_code=status_code,
            content=exc.to_dict(),
        )

    @application.exception_handler(Exception)
    async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled server exception: %s", str(exc))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": (
                        "An unexpected server error occurred."
                        if settings.is_production
                        else str(exc)
                    ),
                }
            },
        )

    # Root Welcome Endpoint
    @application.get(
        "/",
        response_model=WelcomeResponse,
        status_code=status.HTTP_200_OK,
        tags=["System"],
        summary="API Root Welcome",
    )
    async def root() -> WelcomeResponse:
        return WelcomeResponse(
            message=f"Welcome to {settings.app_name} API",
            app_name=settings.app_name,
            version=settings.app_version,
            docs_url="/docs",
        )

    # Register Versioned API Routers
    application.include_router(api_v1_router)

    return application


app = create_application()
