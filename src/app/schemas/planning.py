"""Pydantic v2 schemas and data contracts for dynamic planning and task decomposition."""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class TaskPriority(StrEnum):
    """Priority level determining execution urgency in dependency scheduling."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class PlanStatus(StrEnum):
    """Lifecycle status of an execution plan."""

    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    REVISED = "REVISED"
    OPTIMIZED = "OPTIMIZED"
    REJECTED = "REJECTED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class PlanningRequest(BaseModel):
    """Client request contract to dynamically decompose an objective into an execution plan."""

    workflow_id: str | None = Field(
        default=None,
        description="Optional associated workflow UUID string",
    )
    user_request: str = Field(
        ...,
        min_length=1,
        description="High-level objective or task description to decompose",
    )
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Domain context, parameters, or prior artifacts",
    )
    constraints: list[str] = Field(
        default_factory=list,
        description="Execution constraints (e.g. latency, security, language)",
    )
    max_tasks: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Upper bound on total subtasks generated",
    )
    max_planning_iterations: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum repair iterations allowed if validation fails",
    )
    execution_budget: float | None = Field(
        default=None,
        ge=0.0,
        description="Optional maximum estimated cost or compute budget limit",
    )


class PlannedTask(BaseModel):
    """Discrete executable task unit with explicit dependencies and capability requirements."""

    task_id: str = Field(
        ...,
        min_length=1,
        description="Unique identifier for the task within the plan (e.g. 'task_1')",
    )
    name: str = Field(
        ...,
        min_length=1,
        description="Short human-readable title of the task",
    )
    description: str = Field(
        ...,
        min_length=1,
        description="Detailed execution instructions for the assigned agent",
    )
    task_type: str = Field(
        ...,
        min_length=1,
        description="Classification of work (e.g. 'research', 'analysis', 'coding', 'review')",
    )
    required_capabilities: list[str] = Field(
        default_factory=list,
        description="Agent capabilities required to fulfill this task",
    )
    recommended_agent_id: str = Field(
        ...,
        min_length=1,
        description="Assigned specialized agent identifier (e.g. 'coder', 'researcher')",
    )
    dependencies: list[str] = Field(
        default_factory=list,
        description="List of task_ids that must complete before this task begins",
    )
    priority: TaskPriority = Field(
        default=TaskPriority.MEDIUM,
        description="Scheduling urgency of the task",
    )
    expected_output: str = Field(
        ...,
        min_length=1,
        description="Verifiable deliverable or artifact format expected upon completion",
    )
    estimated_complexity: str = Field(
        default="moderate",
        description="Subjective complexity rating ('low', 'moderate', 'high')",
    )
    execution_constraints: dict[str, Any] = Field(
        default_factory=dict,
        description="Task-specific constraints (e.g. timeout, memory limit)",
    )

    @field_validator("task_id", "name", "recommended_agent_id", "task_type", mode="before")
    @classmethod
    def strip_and_validate_non_empty(cls, v: Any) -> str:
        """Ensure string fields are stripped and non-empty."""
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Field must be a non-empty string.")
        return v.strip()

    @model_validator(mode="after")
    def validate_no_self_dependency(self) -> "PlannedTask":
        """Prevent a task from depending on itself."""
        if self.task_id in self.dependencies:
            raise ValueError(f"Task '{self.task_id}' cannot list itself as a dependency.")
        return self


class ExecutionStage(BaseModel):
    """Grouping of tasks that can execute in parallel during a discrete workflow phase."""

    stage_number: int = Field(
        ...,
        ge=1,
        description="1-based sequence index of the execution stage",
    )
    name: str = Field(
        ...,
        min_length=1,
        description="Descriptive title of the stage (e.g. 'Foundational Research')",
    )
    tasks: list[PlannedTask] = Field(
        default_factory=list,
        description="Planned tasks scheduled for concurrent execution in this stage",
    )
    can_run_parallel: bool = Field(
        default=True,
        description="Whether all tasks within this stage are mutually independent",
    )


class ExecutionPlan(BaseModel):
    """Complete validated and dependency-ordered plan for executing an objective."""

    plan_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier of the execution plan",
    )
    workflow_id: str | None = Field(
        default=None,
        description="Optional associated workflow UUID string",
    )
    objective: str = Field(
        ...,
        min_length=1,
        description="Synthesized high-level objective of the plan",
    )
    tasks: list[PlannedTask] = Field(
        default_factory=list,
        description="Complete list of planned tasks",
    )
    execution_stages: list[ExecutionStage] = Field(
        default_factory=list,
        description="Ordered sequence of parallel execution stages",
    )
    estimated_task_count: int = Field(
        default=0,
        ge=0,
        description="Total count of tasks in the plan",
    )
    plan_status: PlanStatus = Field(
        default=PlanStatus.DRAFT,
        description="Current lifecycle validation and execution status",
    )
    validation_errors: list[str] = Field(
        default_factory=list,
        description="List of validation errors if plan validation failed",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Operational metadata (e.g. planning_duration_ms, iteration_count)",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC creation timestamp",
    )

    @model_validator(mode="after")
    def validate_plan_integrity(self) -> "ExecutionPlan":
        """Validate task identifier uniqueness and dependency existence."""
        task_ids: set[str] = set()
        for t in self.tasks:
            if t.task_id in task_ids:
                raise ValueError(f"Duplicate task_id detected in plan: '{t.task_id}'.")
            task_ids.add(t.task_id)

        # Check dependency references
        for t in self.tasks:
            for dep in t.dependencies:
                if dep not in task_ids:
                    raise ValueError(f"Task '{t.task_id}' references unknown dependency '{dep}'.")

        if not self.estimated_task_count and self.tasks:
            self.estimated_task_count = len(self.tasks)
        return self


class PlanningDecision(BaseModel):
    """High-level summary of planning strategies, agent selections, and concurrency bounds."""

    selected_agents: list[str] = Field(
        default_factory=list,
        description="Unique agent identifiers assigned across all tasks in the plan",
    )
    routing_reason_summary: str = Field(
        ...,
        description="Executive rationale explaining the chosen architecture and delegation",
    )
    execution_strategy: str = Field(
        ...,
        description="Strategy description (e.g. 'Parallel Research followed by Sequential Coding')",
    )
    parallelizable_tasks: list[list[str]] = Field(
        default_factory=list,
        description="Task ID groupings that can execute concurrently",
    )
    blocking_dependencies: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Mapping of task_id to the dependencies that block its execution",
    )
