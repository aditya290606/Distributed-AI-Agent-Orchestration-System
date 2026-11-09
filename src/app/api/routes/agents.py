"""Specialized AI Agent management and individual execution endpoints."""

import logging

from fastapi import APIRouter, Body, Depends, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.contracts import AgentConfig, AgentInput
from app.db.dependencies import get_db
from app.schemas.agent import (
    AgentExecuteRequest,
    AgentExecuteResponse,
    AgentListResponse,
    AgentMetadataResponse,
)
from app.services.agent_execution_service import (
    AgentExecutionService,
    get_agent_execution_service,
)

logger = logging.getLogger("app.api.routes.agents")

router = APIRouter(prefix="/agents", tags=["Specialized Agents"])


@router.get(
    "",
    response_model=AgentListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Registered Agents",
    description="Returns metadata and capabilities for all specialized agents currently registered in the system.",
)
async def list_agents_endpoint(
    service: AgentExecutionService = Depends(get_agent_execution_service),
) -> AgentListResponse:
    """Retrieve all available specialized agents."""
    agents = service.list_agents()
    return AgentListResponse(
        agents=[
            AgentMetadataResponse(
                agent_id=m.agent_id,
                name=m.name,
                role=m.role,
                description=m.description,
                output_schema_name=m.output_schema_name,
                capabilities=m.capabilities,
            )
            for m in agents
        ],
        total_count=len(agents),
    )


@router.get(
    "/{agent_id}",
    response_model=AgentMetadataResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Agent Details",
    description="Returns detailed metadata, capabilities, and schema configuration for a specific specialized agent.",
)
async def get_agent_endpoint(
    agent_id: str = Path(..., description="Unique agent identifier (e.g. 'supervisor', 'planner')"),
    service: AgentExecutionService = Depends(get_agent_execution_service),
) -> AgentMetadataResponse:
    """Retrieve metadata for a specific agent."""
    metadata = service.get_agent_details(agent_id)
    return AgentMetadataResponse(
        agent_id=metadata.agent_id,
        name=metadata.name,
        role=metadata.role,
        description=metadata.description,
        output_schema_name=metadata.output_schema_name,
        capabilities=metadata.capabilities,
    )


@router.post(
    "/{agent_id}/execute",
    response_model=AgentExecuteResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute Specialized Agent",
    description="Invokes an individual specialized agent to execute a task, returning structured deliverables and metrics.",
)
async def execute_agent_endpoint(
    agent_id: str = Path(..., description="Unique agent identifier to execute"),
    payload: AgentExecuteRequest = Body(...),
    service: AgentExecutionService = Depends(get_agent_execution_service),
    db: AsyncSession = Depends(get_db),
) -> AgentExecuteResponse:
    """Execute a single specialized agent with input validation and persistence."""
    agent_input = AgentInput(
        task_id=payload.task_id,
        workflow_id=payload.workflow_id,
        task_description=payload.task_description,
        context=payload.context,
        instructions=payload.instructions,
        expected_output=payload.expected_output,
    )

    agent_config = AgentConfig(
        temperature=payload.temperature,
        max_output_tokens=payload.max_output_tokens,
    )

    output = await service.execute_agent(
        agent_id=agent_id,
        input_data=agent_input,
        config=agent_config,
        session=db,
    )

    return AgentExecuteResponse(
        agent_id=output.agent_id,
        agent_name=output.agent_name,
        status=output.status,
        result=output.result,
        reasoning_summary=output.reasoning_summary,
        execution_metadata=output.execution_metadata,
        error=output.error,
        started_at=output.started_at,
        completed_at=output.completed_at,
    )
