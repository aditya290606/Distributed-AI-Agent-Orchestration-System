"""Worker status, registry inspection, and distributed health API endpoints."""

import logging

from fastapi import APIRouter, status

from app.schemas.execution import WorkerHealthSummaryResponse, WorkerInfoResponse
from app.workers.health import get_worker_health_summary
from app.workers.worker_registry import list_active_workers

logger = logging.getLogger("app.api.routes.workers")

router = APIRouter(prefix="/workers", tags=["Celery Worker Registry & Health"])


@router.get(
    "/health",
    response_model=WorkerHealthSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Distributed Worker Fleet Health",
    description="Probes the Redis message broker and polls all connected Celery worker nodes for health metrics.",
)
async def get_worker_health() -> WorkerHealthSummaryResponse:
    """Return health status across message broker and worker pool."""
    health_data = get_worker_health_summary()
    return WorkerHealthSummaryResponse.model_validate(health_data)


@router.get(
    "",
    response_model=list[WorkerInfoResponse],
    status_code=status.HTTP_200_OK,
    summary="List Active Celery Workers",
    description="Queries the Celery broker inspection API to return all active worker nodes, pools, and assigned queues.",
)
async def get_active_workers() -> list[WorkerInfoResponse]:
    """Return list of active Celery worker nodes and their queue assignments."""
    workers_list = list_active_workers()
    return [WorkerInfoResponse.model_validate(w) for w in workers_list]
