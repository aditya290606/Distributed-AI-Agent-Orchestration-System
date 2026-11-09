"""Standardized Pydantic v2 contracts for specialized agent input and output."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class AgentExecutionStatus(StrEnum):
    """Execution lifecycle status of an agent invocation."""

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    SKIPPED = "SKIPPED"


class AgentConfig(BaseModel):
    """Runtime configuration overrides for agent execution."""

    temperature: float | None = Field(
        default=None,
        ge=0.0,
        le=2.0,
        description="Sampling temperature override",
    )
    max_output_tokens: int | None = Field(
        default=None,
        ge=1,
        le=8192,
        description="Maximum tokens generated in completion",
    )
    model_override: str | None = Field(
        default=None,
        description="Optional model identifier override for this agent",
    )
    timeout_seconds: float | None = Field(
        default=None,
        ge=1.0,
        le=300.0,
        description="Execution deadline timeout in seconds",
    )


class AgentInput(BaseModel):
    """Standardized input contract provided to any specialized agent for execution."""

    task_id: str | None = Field(
        default=None,
        description="Optional unique identifier of the task being executed",
    )
    workflow_id: str | None = Field(
        default=None,
        description="Optional unique identifier of the parent workflow",
    )
    task_description: str = Field(
        ...,
        min_length=1,
        description="Clear, actionable specification of the task the agent must perform",
    )
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured background context, facts, previous outputs, or domain data",
    )
    instructions: str | None = Field(
        default=None,
        description="Specific constraints, guardrails, or procedural guidelines for this run",
    )
    expected_output: str | None = Field(
        default=None,
        description="Specification of the required output format or deliverables",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary caller-supplied metadata (e.g. user_id, tracing tags)",
    )


class AgentOutput(BaseModel):
    """Standardized output container produced upon agent execution completion."""

    agent_id: str = Field(
        ...,
        description="Unique identifier of the agent that performed execution",
    )
    agent_name: str = Field(
        ...,
        description="Human-readable name of the agent",
    )
    status: AgentExecutionStatus = Field(
        ...,
        description="Terminal execution status (SUCCESS, FAILED, PARTIAL_SUCCESS, SKIPPED)",
    )
    result: dict[str, Any] | str = Field(
        ...,
        description="Structured dictionary or formatted string containing the task deliverable",
    )
    reasoning_summary: str = Field(
        ...,
        description=(
            "Concise high-level rationale detailing decisions and conclusions only. "
            "Never exposes internal chain-of-thought or raw scratchpad logs."
        ),
    )
    execution_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Operational metrics (tokens_used, prompt_tokens, duration_ms, cost_usd, model)",
    )
    error: str | None = Field(
        default=None,
        description="Error description if status is FAILED or PARTIAL_SUCCESS, else None",
    )
    started_at: datetime = Field(
        ...,
        description="UTC timestamp marking execution start",
    )
    completed_at: datetime = Field(
        ...,
        description="UTC timestamp marking execution completion",
    )
