"""Fault tolerance, self-healing diagnostics, and recovery REST API endpoints."""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.dependencies import get_db
from app.db.models.recovery import RecoveryEventModel, WorkerNodeModel
from app.db.models.task import Task, TaskStatus
from app.schemas.recovery import (
    ExpiredTaskResponse,
    RecoveryEventResponse,
    RecoveryHealthResponse,
    TaskRecoveryResponse,
    WorkerNodeResponse,
    WorkflowResumeResponse,
)
from app.services.llm.service import LLMService, get_llm_service
from app.services.recovery.task_recovery_manager import TaskRecoveryManager
from app.services.recovery.workflow_recovery import WorkflowRecoveryService

logger = logging.getLogger("app.api.routes.recovery")

router = APIRouter(prefix="/recovery", tags=["Fault Tolerance & Self-Healing"])


def get_task_recovery_manager() -> TaskRecoveryManager:
    """Dependency provider for TaskRecoveryManager."""
    return TaskRecoveryManager()


def get_workflow_recovery_service() -> WorkflowRecoveryService:
    """Dependency provider for WorkflowRecoveryService."""
    return WorkflowRecoveryService()


@router.get(
    "/health",
    response_model=RecoveryHealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Recovery Subsystem Health",
    description="Probes active worker heartbeats, failure detection, and LLM circuit breaker state.",
)
async def get_recovery_health(
    session: AsyncSession = Depends(get_db),
    llm_service: LLMService = Depends(get_llm_service),
) -> RecoveryHealthResponse:
    """Return composite diagnostic health across cluster workers and circuit breaker."""
    now = datetime.now(UTC)
    q = select(WorkerNodeModel)
    res = await session.execute(q)
    nodes = list(res.scalars().all())

    active_count = sum(1 for n in nodes if n.status == "ONLINE")
    unhealthy_count = len(nodes) - active_count

    # Circuit breaker state from primary provider
    cb_state = "CLOSED"
    provider = getattr(llm_service, "provider", None)
    if provider and hasattr(provider, "circuit_breaker"):
        cb_state = provider.circuit_breaker.state.value

    overall = "healthy" if cb_state == "CLOSED" else "degraded"

    return RecoveryHealthResponse(
        status=overall,
        active_workers_count=active_count,
        unhealthy_workers_count=unhealthy_count,
        circuit_breaker_status=cb_state,
        timestamp=now.isoformat(),
    )


@router.get(
    "/workers",
    response_model=list[WorkerNodeResponse],
    status_code=status.HTTP_200_OK,
    summary="List Worker Nodes & Heartbeats",
    description="Returns all registered Celery worker nodes, their recorded heartbeat times, and operational statuses.",
)
async def list_worker_nodes(
    session: AsyncSession = Depends(get_db),
) -> list[WorkerNodeResponse]:
    """Retrieve all cluster worker node records."""
    q = select(WorkerNodeModel).order_by(WorkerNodeModel.last_heartbeat.desc())
    res = await session.execute(q)
    nodes = res.scalars().all()
    return [WorkerNodeResponse.model_validate(n) for n in nodes]


@router.get(
    "/tasks/expired",
    response_model=list[ExpiredTaskResponse],
    status_code=status.HTTP_200_OK,
    summary="List Tasks with Expired Leases",
    description="Returns all tasks currently in RUNNING state whose lease ownership expiration timestamp has passed.",
)
async def list_expired_tasks(
    session: AsyncSession = Depends(get_db),
) -> list[ExpiredTaskResponse]:
    """List tasks whose leases have expired."""
    now = datetime.now(UTC)
    q = (
        select(Task)
        .where(
            Task.status == TaskStatus.RUNNING.value,
            Task.lease_expires_at.is_not(None),
            Task.lease_expires_at < now,
        )
        .order_by(Task.lease_expires_at.asc())
    )
    res = await session.execute(q)
    tasks = res.scalars().all()
    return [
        ExpiredTaskResponse(
            task_id=t.id,
            workflow_id=t.workflow_id,
            planned_task_id=t.planned_task_id,
            name=t.name,
            task_type=t.task_type,
            status=t.status,
            worker_id=t.worker_id,
            lease_token=t.lease_token,
            lease_expires_at=t.lease_expires_at,
            recovery_count=t.recovery_count,
        )
        for t in tasks
    ]


@router.get(
    "/tasks/stuck",
    response_model=list[ExpiredTaskResponse],
    status_code=status.HTTP_200_OK,
    summary="List Stuck Tasks",
    description="Returns tasks that are either expired or in RUNNING state for over 10 minutes without active renewal.",
)
async def list_stuck_tasks(
    session: AsyncSession = Depends(get_db),
) -> list[ExpiredTaskResponse]:
    """List tasks stuck in RUNNING state."""
    now = datetime.now(UTC)
    ten_mins_ago = now - timedelta(minutes=10)
    q = (
        select(Task)
        .where(
            Task.status == TaskStatus.RUNNING.value,
            or_(
                Task.lease_expires_at < now,
                Task.started_at < ten_mins_ago,
            ),
        )
        .order_by(Task.started_at.asc())
    )
    res = await session.execute(q)
    tasks = res.scalars().all()
    return [
        ExpiredTaskResponse(
            task_id=t.id,
            workflow_id=t.workflow_id,
            planned_task_id=t.planned_task_id,
            name=t.name,
            task_type=t.task_type,
            status=t.status,
            worker_id=t.worker_id,
            lease_token=t.lease_token,
            lease_expires_at=t.lease_expires_at,
            recovery_count=t.recovery_count,
        )
        for t in tasks
    ]


@router.post(
    "/tasks/{task_id}",
    response_model=TaskRecoveryResponse,
    status_code=status.HTTP_200_OK,
    summary="Recover an Individual Task",
    description="Manually triggers self-healing recovery on a stuck or expired task.",
)
async def recover_single_task(
    task_id: uuid.UUID = Path(..., description="UUID of the task to recover"),
    session: AsyncSession = Depends(get_db),
    recovery_manager: TaskRecoveryManager = Depends(get_task_recovery_manager),
) -> TaskRecoveryResponse:
    """Manually invoke recovery for a specific task."""
    try:
        outcome = await recovery_manager.recover_task(
            task_id=task_id,
            session=session,
            reason="Manual recovery triggered via API",
        )
        if not outcome:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task with id '{task_id}' not found.",
            )
        return TaskRecoveryResponse.model_validate(outcome)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to recover task '%s': %s", task_id, e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Recovery failed: {e!s}",
        ) from e


@router.post(
    "/workflows/{workflow_id}/resume",
    response_model=WorkflowResumeResponse,
    status_code=status.HTTP_200_OK,
    summary="Resume Interrupted Workflow",
    description="Reconstructs DAG stages and re-dispatches incomplete unblocked tasks while preserving all completed deliverables.",
)
async def resume_workflow(
    workflow_id: uuid.UUID = Path(..., description="UUID of the workflow to resume"),
    session: AsyncSession = Depends(get_db),
    workflow_recovery: WorkflowRecoveryService = Depends(get_workflow_recovery_service),
) -> WorkflowResumeResponse:
    """Safely resume an interrupted workflow execution."""
    try:
        result = await workflow_recovery.resume_workflow(
            workflow_id=workflow_id,
            session=session,
        )
        return WorkflowResumeResponse.model_validate(result)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except Exception as e:
        logger.error("Failed to resume workflow '%s': %s", workflow_id, e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Resumption failed: {e!s}",
        ) from e


@router.get(
    "/workflows/{workflow_id}/history",
    response_model=list[RecoveryEventResponse],
    status_code=status.HTTP_200_OK,
    summary="Workflow Recovery Audit History",
    description="Returns the immutable audit log of all recovery interventions, lease revocations, and retries for a workflow.",
)
async def get_workflow_recovery_history(
    workflow_id: uuid.UUID = Path(..., description="UUID of the workflow"),
    session: AsyncSession = Depends(get_db),
) -> list[RecoveryEventResponse]:
    """Retrieve immutable recovery audit events for a workflow."""
    q = (
        select(RecoveryEventModel)
        .where(RecoveryEventModel.workflow_id == workflow_id)
        .order_by(desc(RecoveryEventModel.created_at))
    )
    res = await session.execute(q)
    events = res.scalars().all()
    return [RecoveryEventResponse.model_validate(e) for e in events]
