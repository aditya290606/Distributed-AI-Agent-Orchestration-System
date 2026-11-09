"""Pydantic API request and response schemas for specialized agent endpoints."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.agents.contracts import AgentExecutionStatus


class AgentMetadataResponse(BaseModel):
    """Metadata response describing a registered agent."""

    agent_id: str = Field(..., description="Unique machine-readable agent identifier")
    name: str = Field(..., description="Human-readable display name")
    role: str = Field(..., description="Primary functional role")
    description: str = Field(..., description="Detailed description of responsibilities")
    output_schema_name: str | None = Field(
        default=None,
        description="Name of the enforced Pydantic output schema, if structured",
    )
    capabilities: list[str] = Field(
        default_factory=list,
        description="List of core capabilities provided by this agent",
    )


class AgentListResponse(BaseModel):
    """Response containing list of all registered agents."""

    agents: list[AgentMetadataResponse] = Field(..., description="List of registered agents")
    total_count: int = Field(..., description="Total number of registered agents")


class AgentExecuteRequest(BaseModel):
    """Request payload to execute an individual specialized agent."""

    task_description: str = Field(
        ...,
        min_length=1,
        max_length=10000,
        description="Actionable description of the task for the agent to execute",
        examples=[
            "Decompose the objective: Build a real-time event streaming pipeline using Kafka and FastAPI."
        ],
    )
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured context, domain knowledge, or reference data",
    )
    instructions: str | None = Field(
        default=None,
        max_length=5000,
        description="Specific constraints, guardrails, or rules for this execution",
    )
    expected_output: str | None = Field(
        default=None,
        max_length=2000,
        description="Specification of the required format or deliverables",
    )
    task_id: str | None = Field(
        default=None,
        description="Optional unique identifier of the task being executed",
    )
    workflow_id: str | None = Field(
        default=None,
        description="Optional unique identifier of the parent workflow",
    )
    temperature: float | None = Field(
        default=None,
        ge=0.0,
        le=2.0,
        description="Optional temperature override (0.0 to 2.0)",
    )
    max_output_tokens: int | None = Field(
        default=None,
        ge=1,
        le=8192,
        description="Optional maximum completion tokens limit",
    )


class AgentExecuteResponse(BaseModel):
    """Response payload containing agent execution outcome and metrics."""

    agent_id: str = Field(..., description="Identifier of the executed agent")
    agent_name: str = Field(..., description="Name of the executed agent")
    status: AgentExecutionStatus = Field(..., description="Execution status")
    result: dict[str, Any] | str = Field(
        ...,
        description="Structured output dictionary or generated deliverable text",
    )
    reasoning_summary: str = Field(
        ...,
        description="Concise rationale explaining key conclusions and decisions without internal chain-of-thought",
    )
    execution_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Operational metrics (tokens_used, duration_ms, cost_usd, model)",
    )
    error: str | None = Field(
        default=None,
        description="Error description if status is FAILED, else None",
    )
    started_at: datetime = Field(..., description="UTC start timestamp")
    completed_at: datetime = Field(..., description="UTC completion timestamp")
