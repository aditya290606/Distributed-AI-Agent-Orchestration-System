"""Pydantic API request and response schemas for workflow orchestration endpoints."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class WorkflowExecuteRequest(BaseModel):
    """Request payload to initiate a multi-agent LangGraph workflow."""

    user_request: str = Field(
        ...,
        min_length=1,
        max_length=10000,
        description="High-level objective or prompt for the multi-agent system",
        examples=["Design and implement a scalable rate-limiting middleware in Python."],
    )
    task_description: str | None = Field(
        default=None,
        max_length=10000,
        description="Optional detailed breakdown of task scope or acceptance criteria",
    )
    workflow_id: uuid.UUID | None = Field(
        default=None,
        description="Optional workflow UUID. If omitted, a new UUID is generated",
    )
    max_revisions: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum allowed reviewer revision loops before forced completion",
    )
    idempotency_key: str | None = Field(
        default=None,
        max_length=128,
        description="Optional client idempotency key to prevent duplicate workflow launches",
    )


class WorkflowExecuteResponse(BaseModel):
    """Response payload containing workflow execution outcome and deliverables."""

    workflow_id: str = Field(..., description="Unique workflow execution identifier")
    status: str = Field(..., description="Terminal execution status (COMPLETED, FAILED)")
    final_output: dict[str, Any] | str | None = Field(
        default=None,
        description="Consolidated report or artifact deliverable produced by the workflow",
    )
    selected_agents: list[str] = Field(
        default_factory=list,
        description="List of specialized agents selected by supervisor/planner",
    )
    completed_agents: list[str] = Field(
        default_factory=list,
        description="Chronological sequence of agents that executed successfully",
    )
    revision_count: int = Field(
        default=0,
        description="Number of reviewer revision cycles executed",
    )
    review_result: dict[str, Any] | None = Field(
        default=None,
        description="Final reviewer audit result and verdict",
    )
    errors: list[str] = Field(
        default_factory=list,
        description="List of error messages encountered during execution, if any",
    )


class TaskResponse(BaseModel):
    """Details of an individual task execution record within a workflow."""

    id: uuid.UUID = Field(..., description="Unique task identifier")
    workflow_id: uuid.UUID = Field(..., description="Parent workflow identifier")
    name: str = Field(..., description="Task title or name")
    task_type: str = Field(..., description="Agent or subtask classification")
    status: str = Field(..., description="Task execution status")
    result_data: dict[str, Any] | None = Field(
        default=None, description="Output payload from subtask"
    )
    error_message: str | None = Field(default=None, description="Error trace if subtask failed")
    created_at: datetime = Field(..., description="Task creation timestamp")
    updated_at: datetime = Field(..., description="Task update timestamp")


class WorkflowResponse(BaseModel):
    """Full workflow record details including database status and deliverables."""

    id: uuid.UUID = Field(..., description="Workflow UUID")
    name: str = Field(..., description="Human-readable workflow name")
    description: str | None = Field(default=None, description="Workflow objective or description")
    status: str = Field(..., description="Execution status (PENDING, RUNNING, COMPLETED, FAILED)")
    input_data: dict[str, Any] = Field(default_factory=dict, description="Initial input parameters")
    result_data: dict[str, Any] | None = Field(
        default=None, description="Completed execution deliverable"
    )
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")


class WorkflowStateResponse(BaseModel):
    """Current state checkpoint inspection for an active or completed workflow."""

    workflow_id: str = Field(..., description="Workflow identifier")
    has_checkpoint: bool = Field(..., description="Whether a LangGraph checkpoint was found")
    checkpoint_state: dict[str, Any] | None = Field(
        default=None,
        description="Raw channel values from the latest graph state checkpoint",
    )
