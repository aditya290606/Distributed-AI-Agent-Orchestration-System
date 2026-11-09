"""Health and readiness diagnostic endpoints."""

from fastapi import APIRouter, Depends, Response, status

from app.core.config import Settings, get_settings
from app.db.session import check_database_health
from app.schemas.health import HealthResponse, ReadinessResponse

router = APIRouter(prefix="/health", tags=["Health Diagnostics"])


@router.get(
    "",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Application Health Check",
    description="Returns the operational status and environment metadata of the system process (liveness probe).",
)
async def check_health(
    settings: Settings = Depends(get_settings),
) -> HealthResponse:
    """Return structured health diagnostic details (process liveness)."""
    return HealthResponse(
        status="healthy",
        app_name=settings.app_name,
        api_version=settings.api_version,
        version=settings.app_version,
        environment=settings.environment,
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Application Readiness Probe",
    description="Probes whether the service is ready to accept user requests by evaluating active MySQL connectivity.",
)
async def check_readiness(response: Response) -> ReadinessResponse:
    """Return system readiness and dependencies status (readiness probe).

    Executes a non-blocking SELECT 1 ping against MySQL. Returns 200 if connected,
    or 503 Service Unavailable if the database is unreachable.
    """
    db_healthy = await check_database_health(timeout_seconds=2.0)

    if db_healthy:
        db_status = "connected"
        overall_status = "ready"
        message = "All critical infrastructure dependencies are healthy and ready."
        response.status_code = status.HTTP_200_OK
    else:
        db_status = "disconnected"
        overall_status = "not_ready"
        message = "Database is unreachable. Service is not ready to accept traffic."
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadinessResponse(
        status=overall_status,
        dependencies={
            "database": db_status,
            "redis": "not_configured (scheduled for subsequent milestone)",
        },
        message=message,
    )
