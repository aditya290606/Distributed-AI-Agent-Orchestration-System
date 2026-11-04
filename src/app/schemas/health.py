"""Pydantic schemas for system health, readiness, and diagnostics."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


class HealthCheckResponse(BaseModel):
    """Payload returned by the system health check endpoints."""

    status: Literal["healthy", "degraded", "unhealthy"] = Field(
        default="healthy",
        description="Overall service operational health status",
    )
    app_name: str = Field(description="Name of the service")
    version: str = Field(description="Active semantic version")
    environment: str = Field(description="Active deployment environment")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp of the health check evaluation",
    )
