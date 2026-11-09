"""Pydantic schemas for distributed task execution and Celery worker endpoints."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DispatchedTaskItem(BaseModel):
    """Metadata regarding an individual task dispatched to Celery."""

    model_config = ConfigDict(from_attributes=True)

    task_id: str = Field(..., description="Durable database Task UUID")
    planned_task_id: str = Field(..., description="Plan DAG task identifier (e.g. task_01)")
    task_type: str = Field(..., description="Task classification / specialized agent type")
    queue: str = Field(..., description="Target Celery queue name")
    celery_task_id: str = Field(..., description="Celery asynchronous job ID")


class ExecutionSubmitResponse(BaseModel):
    """Response payload returned when an execution plan is dispatched to workers."""

    model_config = ConfigDict(from_attributes=True)

    workflow_id: str = Field(..., description="Associated workflow UUID")
    plan_id: str = Field(..., description="Execution plan UUID that was dispatched")
    dispatched_stage: int = Field(..., description="Stage sequence number dispatched")
    dispatched_tasks_count: int = Field(..., description="Number of tasks sent to Celery queues")
    dispatched_tasks: list[DispatchedTaskItem] = Field(
        default_factory=list, description="List of dispatched tasks"
    )
    status: str = Field(
        ..., description="Dispatch outcome status (DISPATCHED, NO_TASKS_DISPATCHED)"
    )


class ExecutionSummaryResponse(BaseModel):
    """Aggregated status and progress summary for a distributed workflow execution."""

    model_config = ConfigDict(from_attributes=True)

    workflow_id: str = Field(..., description="Workflow UUID")
    status: str = Field(..., description="Overall workflow status (RUNNING, COMPLETED, FAILED)")
    total_tasks: int = Field(..., description="Total tasks registered for this workflow")
    tasks_by_status: dict[str, int] = Field(
        default_factory=dict,
        description="Count of tasks grouped by status (QUEUED, RUNNING, SUCCEEDED, FAILED, RETRYING, PENDING)",
    )
    started_at: datetime | None = Field(
        default=None, description="Workflow creation or start timestamp"
    )
    completed_at: datetime | None = Field(
        default=None, description="Workflow terminal completion timestamp"
    )
    elapsed_seconds: float | None = Field(
        default=None, description="Elapsed execution duration in seconds"
    )


class ExecutionTaskItemResponse(BaseModel):
    """Summary representation of a single task within an execution workflow."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Task UUID")
    workflow_id: uuid.UUID = Field(..., description="Parent workflow UUID")
    plan_id: uuid.UUID | None = Field(default=None, description="Execution plan UUID")
    planned_task_id: str | None = Field(default=None, description="DAG task identifier")
    name: str = Field(..., description="Task name")
    task_type: str = Field(..., description="Task classification")
    status: str = Field(..., description="Execution status")
    celery_task_id: str | None = Field(default=None, description="Celery job ID")
    worker_id: str | None = Field(
        default=None, description="Celery worker node that claimed the task"
    )
    retry_count: int = Field(default=0, description="Number of execution attempts")
    started_at: datetime | None = Field(
        default=None, description="Execution commencement timestamp"
    )
    completed_at: datetime | None = Field(
        default=None, description="Execution completion timestamp"
    )
    created_at: datetime = Field(..., description="Record creation timestamp")
    updated_at: datetime = Field(..., description="Record last update timestamp")


class ExecutionTaskDetailResponse(ExecutionTaskItemResponse):
    """Full task detail including input parameters, results, and diagnostics."""

    description: str | None = Field(default=None, description="Detailed task specification")
    input_data: dict[str, Any] | None = Field(default=None, description="Task execution arguments")
    result_data: dict[str, Any] | None = Field(
        default=None, description="Completed task output payload"
    )
    error_message: str | None = Field(default=None, description="Error diagnostics if task failed")


class BrokerHealthResponse(BaseModel):
    """Redis message broker diagnostic health status."""

    model_config = ConfigDict(from_attributes=True)

    status: str = Field(..., description="Broker status (healthy, unreachable, unhealthy)")
    broker_url: str = Field(..., description="Redacted broker connection URL")
    redis_version: str | None = Field(default=None, description="Redis server version")
    connected_clients: int | None = Field(default=None, description="Active client connections")
    error: str | None = Field(default=None, description="Error message if unreachable")


class WorkerInfoResponse(BaseModel):
    """Status and specification of an active Celery worker node."""

    model_config = ConfigDict(from_attributes=True)

    worker_name: str = Field(..., description="Unique Celery worker node hostname")
    status: str = Field(..., description="Worker operational status (online, unknown)")
    active_tasks_count: int = Field(default=0, description="Number of currently running tasks")
    active_tasks: list[dict[str, Any]] = Field(
        default_factory=list, description="Currently running task items"
    )
    queues: list[str] = Field(
        default_factory=list, description="Logical queues monitored by this worker"
    )
    concurrency: int = Field(default=1, description="Worker pool concurrency / processes")
    pool_type: str = Field(
        default="solo", description="Worker execution pool type (solo, prefork, gevent)"
    )
    last_seen: str = Field(..., description="Last heartbeat check timestamp")


class WorkerHealthSummaryResponse(BaseModel):
    """Consolidated health report across message broker and worker pool."""

    model_config = ConfigDict(from_attributes=True)

    status: str = Field(..., description="Overall distributed health (healthy, degraded)")
    timestamp: str = Field(..., description="Report generation timestamp")
    broker: BrokerHealthResponse = Field(..., description="Redis broker diagnostics")
    workers_online: int = Field(default=0, description="Count of reachable worker nodes")
    workers: list[WorkerInfoResponse] = Field(
        default_factory=list, description="Active worker details"
    )
