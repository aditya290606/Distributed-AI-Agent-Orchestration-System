"""Workflow orchestration and state management API endpoints."""

import logging
import uuid

from fastapi import APIRouter, Body, Depends, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ResourceNotFoundError
from app.db.dependencies import get_db
from app.orchestration.checkpointer import (
    WorkflowCheckpointAdapter,
    get_checkpoint_adapter,
)
from app.orchestration.executor import (
    WorkflowExecutor,
    get_workflow_executor,
)
from app.schemas.workflow import (
    TaskResponse,
    WorkflowExecuteRequest,
    WorkflowExecuteResponse,
    WorkflowResponse,
    WorkflowStateResponse,
)
from app.services.workflow_service import (
    get_tasks_by_workflow_id,
    get_workflow_by_id,
)

logger = logging.getLogger("app.api.routes.workflows")

router = APIRouter(prefix="/workflows", tags=["Workflow Orchestration"])


@router.post(
    "/execute",
    response_model=WorkflowExecuteResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute Multi-Agent Workflow",
    description="Initiates and executes a stateful multi-agent LangGraph workflow for an objective.",
)
async def execute_workflow_endpoint(
    payload: WorkflowExecuteRequest = Body(...),
    executor: WorkflowExecutor = Depends(get_workflow_executor),
    db: AsyncSession = Depends(get_db),
) -> WorkflowExecuteResponse:
    """Trigger multi-agent orchestration for the given objective."""
    logger.info("Received request to execute multi-agent workflow: '%s'", payload.user_request[:60])

    result = await executor.execute_workflow(
        user_request=payload.user_request,
        workflow_id=payload.workflow_id,
        task_description=payload.task_description,
        max_revisions=payload.max_revisions,
        session=db,
    )

    return WorkflowExecuteResponse(
        workflow_id=result["workflow_id"],
        status=result["status"],
        final_output=result.get("final_output"),
        selected_agents=result.get("selected_agents", []),
        completed_agents=result.get("completed_agents", []),
        revision_count=result.get("revision_count", 0),
        review_result=result.get("review_result"),
        errors=result.get("errors", []),
    )


@router.get(
    "/{workflow_id}",
    response_model=WorkflowResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Workflow Record",
    description="Retrieves a persisted workflow record by its UUID, including lifecycle status and deliverables.",
)
async def get_workflow_endpoint(
    workflow_id: uuid.UUID = Path(..., description="Unique UUID identifier of the workflow"),
    db: AsyncSession = Depends(get_db),
) -> WorkflowResponse:
    """Retrieve full workflow record from the database."""
    workflow = await get_workflow_by_id(db, workflow_id)
    if workflow is None:
        raise ResourceNotFoundError(resource="Workflow", resource_id=str(workflow_id))

    return WorkflowResponse(
        id=workflow.id,
        name=workflow.name,
        description=workflow.description,
        status=workflow.status,
        input_data=workflow.input_data,
        result_data=workflow.result_data,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
    )


@router.get(
    "/{workflow_id}/state",
    response_model=WorkflowStateResponse,
    status_code=status.HTTP_200_OK,
    summary="Inspect Workflow Checkpoint State",
    description="Inspects the latest LangGraph channel state checkpoint for a workflow.",
)
async def get_workflow_state_endpoint(
    workflow_id: uuid.UUID = Path(..., description="Unique UUID identifier of the workflow"),
    checkpointer: WorkflowCheckpointAdapter = Depends(get_checkpoint_adapter),
) -> WorkflowStateResponse:
    """Retrieve the latest in-memory/graph checkpoint state."""
    checkpoint_state = await checkpointer.get_latest_checkpoint(str(workflow_id))
    return WorkflowStateResponse(
        workflow_id=str(workflow_id),
        has_checkpoint=checkpoint_state is not None,
        checkpoint_state=checkpoint_state,
    )


@router.get(
    "/{workflow_id}/tasks",
    response_model=list[TaskResponse],
    status_code=status.HTTP_200_OK,
    summary="List Workflow Tasks",
    description="Retrieves all subtask executions and deliverables created during workflow orchestration.",
)
async def get_workflow_tasks_endpoint(
    workflow_id: uuid.UUID = Path(..., description="Unique UUID identifier of the workflow"),
    db: AsyncSession = Depends(get_db),
) -> list[TaskResponse]:
    """Retrieve chronological subtask records associated with the workflow."""
    tasks = await get_tasks_by_workflow_id(db, workflow_id)
    return [
        TaskResponse(
            id=t.id,
            workflow_id=t.workflow_id,
            name=t.name,
            task_type=t.task_type,
            status=t.status,
            result_data=t.result_data,
            error_message=t.error_message,
            created_at=t.created_at,
            updated_at=t.updated_at,
        )
        for t in tasks
    ]
