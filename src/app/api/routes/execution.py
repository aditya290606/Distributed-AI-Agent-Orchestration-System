"""Execution routes for dispatching execution plans and monitoring distributed Celery task progress."""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.dependencies import get_db
from app.db.models.task import Task
from app.db.models.workflow import Workflow
from app.schemas.execution import (
    ExecutionSubmitResponse,
    ExecutionSummaryResponse,
    ExecutionTaskDetailResponse,
    ExecutionTaskItemResponse,
)
from app.schemas.recovery import WorkflowCancelResponse
from app.services.recovery.cancellation import WorkflowCancellationService
from app.services.task_dispatcher import DistributedTaskDispatcher

logger = logging.getLogger("app.api.routes.execution")

router = APIRouter(prefix="/execution", tags=["Distributed Task Execution"])


@router.post(
    "/submit/{plan_id}",
    response_model=ExecutionSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit Execution Plan to Distributed Workers",
    description="Validates an execution plan, provisions workflow tracking, and dispatches initial stage tasks to Celery.",
)
async def submit_execution_plan(
    plan_id: uuid.UUID = Path(..., description="UUID of the execution plan to dispatch"),
    session: AsyncSession = Depends(get_db),
    dispatcher: DistributedTaskDispatcher = Depends(DistributedTaskDispatcher),
) -> ExecutionSubmitResponse:
    """Submit an execution plan to Celery worker queues."""
    logger.info("Received request to submit execution plan '%s' to distributed workers", plan_id)
    try:
        result = await dispatcher.dispatch_plan(plan_id=plan_id, session=session)
        await session.commit()
        return ExecutionSubmitResponse(**result)
    except ValueError as e:
        err_msg = str(e)
        logger.warning("Invalid execution plan submission '%s': %s", plan_id, err_msg)
        if "does not exist" in err_msg:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=err_msg) from e
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err_msg) from e
    except Exception as e:
        logger.error("Failed to submit execution plan '%s': %s", plan_id, e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to submit execution plan: {e!s}",
        ) from e


@router.get(
    "/{workflow_id}",
    response_model=ExecutionSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Distributed Workflow Execution Summary",
    description="Retrieves the current execution progress, duration, and task status aggregation for a workflow.",
)
async def get_execution_summary(
    workflow_id: uuid.UUID = Path(..., description="UUID of the workflow execution"),
    session: AsyncSession = Depends(get_db),
) -> ExecutionSummaryResponse:
    """Retrieve high-level progress and task counts for an active or completed workflow."""
    wf_query = select(Workflow).where(Workflow.id == workflow_id)
    wf_res = await session.execute(wf_query)
    wf = wf_res.scalar_one_or_none()
    if not wf:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow with id '{workflow_id}' not found.",
        )

    # Count tasks grouped by status
    status_query = (
        select(Task.status, func.count(Task.id))
        .where(Task.workflow_id == workflow_id)
        .group_by(Task.status)
    )
    status_res = await session.execute(status_query)
    tasks_by_status = {str(s): count for s, count in status_res.all()}
    total_tasks = sum(tasks_by_status.values())

    started_at = wf.created_at
    completed_at = wf.updated_at if wf.status in ("COMPLETED", "FAILED") else None
    elapsed_seconds = None
    if started_at:
        end_time = completed_at if completed_at else datetime.now(UTC)
        if started_at.tzinfo is None and end_time.tzinfo is not None:
            started_at = started_at.replace(tzinfo=UTC)
        elapsed_seconds = max(0.0, (end_time - started_at).total_seconds())

    return ExecutionSummaryResponse(
        workflow_id=str(wf.id),
        status=wf.status,
        total_tasks=total_tasks,
        tasks_by_status=tasks_by_status,
        started_at=started_at,
        completed_at=completed_at,
        elapsed_seconds=round(elapsed_seconds, 2) if elapsed_seconds is not None else None,
    )


@router.get(
    "/{workflow_id}/tasks",
    response_model=list[ExecutionTaskItemResponse],
    status_code=status.HTTP_200_OK,
    summary="List Distributed Tasks for Workflow",
    description="Returns detailed records for all distributed tasks belonging to a workflow execution.",
)
async def list_workflow_tasks(
    workflow_id: uuid.UUID = Path(..., description="UUID of the workflow execution"),
    session: AsyncSession = Depends(get_db),
) -> list[ExecutionTaskItemResponse]:
    """List all individual task tracking records associated with a workflow."""
    wf_query = select(Workflow).where(Workflow.id == workflow_id)
    wf_res = await session.execute(wf_query)
    if not wf_res.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow with id '{workflow_id}' not found.",
        )

    tasks_query = (
        select(Task).where(Task.workflow_id == workflow_id).order_by(Task.created_at.asc())
    )
    tasks_res = await session.execute(tasks_query)
    tasks = tasks_res.scalars().all()
    return [ExecutionTaskItemResponse.model_validate(t) for t in tasks]


@router.get(
    "/tasks/{task_id}",
    response_model=ExecutionTaskDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Distributed Task Details",
    description="Retrieves full execution diagnostics, input/output data, worker assignment, and retries for a task.",
)
async def get_task_detail(
    task_id: uuid.UUID = Path(..., description="UUID of the task"),
    session: AsyncSession = Depends(get_db),
) -> ExecutionTaskDetailResponse:
    """Retrieve full execution record and diagnostics for an individual task."""
    task_query = select(Task).where(Task.id == task_id)
    task_res = await session.execute(task_query)
    task = task_res.scalar_one_or_none()
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task with id '{task_id}' not found.",
        )
    return ExecutionTaskDetailResponse.model_validate(task)


def get_cancellation_service() -> WorkflowCancellationService:
    """Dependency provider for WorkflowCancellationService."""
    return WorkflowCancellationService()


@router.post(
    "/{workflow_id}/cancel",
    response_model=WorkflowCancelResponse,
    status_code=status.HTTP_200_OK,
    summary="Cancel Distributed Workflow Execution",
    description="Safely revokes active Celery tasks and marks remaining tasks as CANCELLED while preserving completed deliverables.",
)
async def cancel_workflow_execution(
    workflow_id: uuid.UUID = Path(..., description="UUID of the workflow execution to cancel"),
    session: AsyncSession = Depends(get_db),
    cancellation_service: WorkflowCancellationService = Depends(get_cancellation_service),
) -> WorkflowCancelResponse:
    """Cancel an active workflow execution safely."""
    try:
        result = await cancellation_service.cancel_workflow(
            workflow_id=workflow_id,
            session=session,
            reason="User cancellation requested via API",
        )
        return WorkflowCancelResponse.model_validate(result)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except Exception as e:
        logger.error("Failed to cancel workflow '%s': %s", workflow_id, e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cancel workflow: {e!s}",
        ) from e
