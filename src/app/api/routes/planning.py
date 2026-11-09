"""API route endpoints for Dynamic Planning, Task Decomposition, and Execution Plans."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.dependencies import get_db
from app.schemas.planning import (
    ExecutionPlan,
    ExecutionStage,
    PlannedTask,
    PlanningRequest,
)
from app.services.planning_service import PlanningService, get_planning_service

logger = logging.getLogger("app.api.routes.planning")

router = APIRouter(prefix="/planning", tags=["Planning Engine"])


@router.post(
    "/generate",
    response_model=ExecutionPlan,
    status_code=status.HTTP_200_OK,
    summary="Generate an execution plan",
    description="Decomposes a complex objective into a validated, dependency-ordered execution plan.",
)
async def generate_execution_plan(
    request: PlanningRequest,
    planning_service: PlanningService = Depends(get_planning_service),
    session: AsyncSession = Depends(get_db),
) -> ExecutionPlan:
    """Generate, validate, optimize, and persist a structured execution plan."""
    logger.info("Received request to generate execution plan for: '%s'", request.user_request[:60])
    try:
        plan = await planning_service.generate_plan(request=request, session=session)
        return plan
    except ValueError as e:
        logger.warning("Planning request validation or graph error: %s", e)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        ) from e
    except Exception as e:
        logger.error("Unexpected error during planning execution: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate execution plan due to an internal error.",
        ) from e


@router.get(
    "/{plan_id}",
    response_model=ExecutionPlan,
    status_code=status.HTTP_200_OK,
    summary="Retrieve an execution plan",
    description="Fetch a persisted execution plan by its unique UUID.",
)
async def get_execution_plan(
    plan_id: uuid.UUID,
    planning_service: PlanningService = Depends(get_planning_service),
    session: AsyncSession = Depends(get_db),
) -> ExecutionPlan:
    """Retrieve full execution plan summary and status."""
    plan = await planning_service.get_plan_by_id(plan_id=plan_id, session=session)
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution plan with id '{plan_id}' was not found.",
        )
    return plan


@router.get(
    "/{plan_id}/tasks",
    response_model=list[PlannedTask],
    status_code=status.HTTP_200_OK,
    summary="List tasks in an execution plan",
    description="Retrieve all planned tasks with capabilities and dependencies for a given plan.",
)
async def get_plan_tasks(
    plan_id: uuid.UUID,
    planning_service: PlanningService = Depends(get_planning_service),
    session: AsyncSession = Depends(get_db),
) -> list[PlannedTask]:
    """Retrieve list of planned tasks for a plan."""
    tasks = await planning_service.get_plan_tasks(plan_id=plan_id, session=session)
    if tasks is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution plan with id '{plan_id}' was not found.",
        )
    return tasks


@router.get(
    "/{plan_id}/stages",
    response_model=list[ExecutionStage],
    status_code=status.HTTP_200_OK,
    summary="List execution stages in an execution plan",
    description="Retrieve parallel execution stages for a given plan.",
)
async def get_plan_stages(
    plan_id: uuid.UUID,
    planning_service: PlanningService = Depends(get_planning_service),
    session: AsyncSession = Depends(get_db),
) -> list[ExecutionStage]:
    """Retrieve execution stages for a plan."""
    stages = await planning_service.get_plan_stages(plan_id=plan_id, session=session)
    if stages is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution plan with id '{plan_id}' was not found.",
        )
    return stages
