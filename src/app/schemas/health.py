"""Pydantic schemas for root welcome, system health, and readiness probes."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


class WelcomeResponse(BaseModel):
    """Payload returned by the root API endpoint."""

    message: str = Field(description="Welcome message")
    app_name: str = Field(description="Name of the service")
    version: str = Field(description="Active semantic version")
    docs_url: str = Field(description="URL to the interactive API documentation")


class HealthResponse(BaseModel):
    """Payload returned by the primary health check endpoint."""

    status: Literal["healthy", "degraded", "unhealthy"] = Field(
        default="healthy",
        description="Overall service operational health status",
    )
    app_name: str = Field(description="Name of the application")
    api_version: str = Field(default="v1", description="Active API route version")
    version: str = Field(default="0.1.0", description="Semantic application version")
    environment: str = Field(description="Active deployment environment")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp of the health check evaluation",
    )


class ReadinessResponse(BaseModel):
    """Payload returned by the readiness probe endpoint."""

    status: Literal["ready", "not_ready"] = Field(
        default="ready",
        description="Readiness status indicating if service can accept traffic",
    )
    dependencies: dict[str, str] = Field(
        default_factory=lambda: {
            "database": "not_configured (scheduled for subsequent milestone)",
            "redis": "not_configured (scheduled for subsequent milestone)",
        },
        description="Status of external subsystem dependencies",
    )
    message: str = Field(
        default="Service is ready to accept requests",
        description="Diagnostic summary message",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp of the readiness check evaluation",
    )


# Alias for backward compatibility with existing tests
HealthCheckResponse = HealthResponse
