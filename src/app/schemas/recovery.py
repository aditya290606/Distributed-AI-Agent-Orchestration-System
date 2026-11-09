"""Pydantic schemas for fault tolerance, worker heartbeat, task recovery, and cancellation."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RecoveryHealthResponse(BaseModel):
    """System-wide fault tolerance and self-healing diagnostic status."""

    model_config = ConfigDict(from_attributes=True)

    status: str = Field(..., description="Overall recovery subsystem status (healthy, degraded)")
    active_workers_count: int = Field(
        default=0, description="Count of currently healthy online workers"
    )
    unhealthy_workers_count: int = Field(
        default=0, description="Count of suspected or offline workers"
    )
    circuit_breaker_status: str = Field(
        default="CLOSED", description="LLM provider circuit breaker state"
    )
    timestamp: str = Field(..., description="ISO diagnostic timestamp")


class WorkerNodeResponse(BaseModel):
    """Details of a registered Celery worker node in the cluster."""

    model_config = ConfigDict(from_attributes=True)

    worker_id: str = Field(..., description="Unique worker node identifier")
    hostname: str | None = Field(default=None, description="Worker host machine name or IP")
    status: str = Field(..., description="Operational status (ONLINE, SUSPECTED, OFFLINE)")
    last_heartbeat: datetime = Field(..., description="Last received heartbeat timestamp")
    registered_queues: list[str] = Field(
        default_factory=list, description="Monitored Celery queues"
    )
    active_task_count: int = Field(default=0, description="Tasks currently being executed")
    startup_time: datetime | None = Field(default=None, description="Worker boot timestamp")


class ExpiredTaskResponse(BaseModel):
    """Task item whose lease has expired or is stuck in RUNNING state."""

    model_config = ConfigDict(from_attributes=True)

    task_id: uuid.UUID = Field(..., description="Task UUID")
    workflow_id: uuid.UUID = Field(..., description="Parent workflow UUID")
    planned_task_id: str | None = Field(default=None, description="Plan task identifier")
    name: str = Field(..., description="Task title")
    task_type: str = Field(..., description="Task agent specialization")
    status: str = Field(..., description="Current status")
    worker_id: str | None = Field(default=None, description="Worker that held the lease")
    lease_token: str | None = Field(default=None, description="Lease ownership token")
    lease_expires_at: datetime | None = Field(
        default=None, description="Lease expiration timestamp"
    )
    recovery_count: int = Field(default=0, description="Previous recovery attempts")


class TaskRecoveryResponse(BaseModel):
    """Outcome of an automated or manual recovery action applied to a task."""

    model_config = ConfigDict(from_attributes=True)

    task_id: str = Field(..., description="Recovered Task UUID")
    decision: str = Field(
        ..., description="Recovery action applied (REQUEUE, MARK_FAILED, MARK_CANCELLED)"
    )
    new_status: str | None = Field(default=None, description="Task status after intervention")
    recovery_count: int | None = Field(default=None, description="Updated recovery attempt counter")
    celery_task_id: str | None = Field(default=None, description="New Celery job ID if requeued")
    reason: str | None = Field(default=None, description="Failure reason or diagnostic note")


class WorkflowResumeResponse(BaseModel):
    """Outcome of resuming an interrupted workflow."""

    model_config = ConfigDict(from_attributes=True)

    workflow_id: str = Field(..., description="Workflow UUID")
    status: str = Field(..., description="Workflow execution status")
    preserved_tasks_count: int = Field(
        ..., description="Count of already completed subtasks preserved"
    )
    preserved_tasks: list[str] = Field(
        default_factory=list, description="List of preserved task identifiers"
    )
    resumed_tasks_count: int = Field(..., description="Count of tasks unblocked and re-dispatched")
    resumed_tasks: list[str] = Field(
        default_factory=list, description="List of resumed task identifiers"
    )
    blocked_tasks_count: int = Field(
        ..., description="Count of tasks blocked due to failed prerequisites"
    )
    blocked_tasks: list[str] = Field(
        default_factory=list, description="List of blocked task identifiers"
    )


class WorkflowCancelResponse(BaseModel):
    """Outcome of safely cancelling an active workflow and revoking worker tasks."""

    model_config = ConfigDict(from_attributes=True)

    workflow_id: str = Field(..., description="Workflow UUID")
    status: str = Field(..., description="Final terminal status (CANCELLED)")
    cancelled_tasks_count: int = Field(
        ..., description="Number of active or pending tasks cancelled"
    )
    preserved_tasks_count: int = Field(
        ..., description="Number of already succeeded tasks preserved"
    )
    reason: str = Field(..., description="Cancellation justification")


class RecoveryEventResponse(BaseModel):
    """Immutable audit record of a past recovery event."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Recovery event UUID")
    workflow_id: uuid.UUID = Field(..., description="Workflow UUID")
    task_id: uuid.UUID = Field(..., description="Task UUID")
    previous_status: str = Field(..., description="Status before recovery")
    new_status: str = Field(..., description="Status after recovery")
    recovery_reason: str = Field(..., description="Condition that triggered recovery")
    recovery_action: str = Field(..., description="Action taken")
    worker_id: str | None = Field(default=None, description="Worker holding task")
    retry_count: int = Field(default=0, description="Retry count at time of recovery")
    error_category: str | None = Field(default=None, description="Error classification")
    created_at: datetime = Field(..., description="Event timestamp")
