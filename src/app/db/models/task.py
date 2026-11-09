"""Task database model and status definitions."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, types
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.execution_plan import ExecutionPlanModel
    from app.db.models.workflow import Workflow


class TaskStatus(StrEnum):
    """Execution status lifecycle for an individual Task."""

    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    CANCELLED = "CANCELLED"


class Task(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Represents a discrete executable task unit within a Workflow."""

    __tablename__ = "tasks"

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        types.Uuid(as_uuid=True),
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Foreign key referencing the parent workflow",
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment="Human-readable subtask name",
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Specific instructions or parameters for this subtask",
    )
    task_type: Mapped[str] = mapped_column(
        String(100),
        default="generic",
        nullable=False,
        index=True,
        comment="Agent specialization required (e.g. planner, researcher, coder)",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default=TaskStatus.PENDING.value,
        nullable=False,
        index=True,
        comment="Current execution lifecycle status",
    )
    input_data: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
        comment="Input payload required for task execution",
    )
    result_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        comment="Output payload produced by task execution",
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Error trace or failure reason if status is FAILED",
    )
    plan_id: Mapped[uuid.UUID | None] = mapped_column(
        types.Uuid(as_uuid=True),
        ForeignKey("execution_plans.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Optional foreign key referencing the associated execution plan",
    )
    planned_task_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
        comment="Planned task string identifier from the execution plan (e.g. 'task_1')",
    )
    celery_task_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
        comment="Celery asynchronous task UUID string",
    )
    worker_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Worker identifier or hostname that processed the task",
    )
    retry_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="Number of times execution has been retried",
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="UTC timestamp when worker execution began",
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="UTC timestamp when execution concluded",
    )
    lease_token: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
        comment="Cryptographic token identifying active lease ownership",
    )
    lease_acquired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="UTC timestamp when the lease was claimed",
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        comment="UTC timestamp when current lease validity expires",
    )
    lease_renewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="UTC timestamp when lease was most recently renewed",
    )
    recovery_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="Number of times this task was automatically recovered from failure",
    )
    last_error_category: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="Classification of the most recent error (TRANSIENT, PERMANENT, TIMEOUT)",
    )

    # Relationships
    workflow: Mapped["Workflow"] = relationship(
        "Workflow",
        back_populates="tasks",
        lazy="selectin",
    )
    execution_plan: Mapped["ExecutionPlanModel | None"] = relationship(
        "ExecutionPlanModel",
        lazy="selectin",
    )
