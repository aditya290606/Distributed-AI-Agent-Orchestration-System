"""Workflow database model and status definitions."""

from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.execution_plan import ExecutionPlanModel
    from app.db.models.task import Task


class WorkflowStatus(StrEnum):
    """Execution status lifecycle for a Workflow."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    PAUSED = "PAUSED"


class Workflow(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Represents a multi-agent orchestration workflow instance."""

    __tablename__ = "workflows"

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment="Human-readable workflow name",
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Detailed workflow description or user objective",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default=WorkflowStatus.PENDING.value,
        nullable=False,
        index=True,
        comment="Current execution lifecycle status",
    )
    input_data: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
        comment="Input parameters and configuration for the workflow",
    )
    result_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        comment="Final output deliverable or error details upon completion",
    )

    # Relationships
    tasks: Mapped[list["Task"]] = relationship(
        "Task",
        back_populates="workflow",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="Task.created_at",
    )
    execution_plans: Mapped[list["ExecutionPlanModel"]] = relationship(
        "ExecutionPlanModel",
        back_populates="workflow",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ExecutionPlanModel.created_at",
    )
