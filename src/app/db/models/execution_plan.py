"""Database models for Execution Plans and Planning Iterations."""

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, types
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.workflow import Workflow


class ExecutionPlanModel(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Database persistence model for structured execution plans."""

    __tablename__ = "execution_plans"

    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        types.Uuid(as_uuid=True),
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="Optional foreign key referencing the associated parent workflow",
    )
    objective: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Normalized objective of the execution plan",
    )
    plan_status: Mapped[str] = mapped_column(
        String(32),
        default="DRAFT",
        nullable=False,
        index=True,
        comment="Lifecycle validation status (DRAFT, VALIDATED, REVISED, OPTIMIZED, REJECTED, etc.)",
    )
    task_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="Total count of planned subtasks",
    )
    tasks_data: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
        comment="Complete JSON serialization of planned tasks",
    )
    stages_data: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
        comment="JSON serialization of parallel execution stages",
    )
    plan_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
        comment="Operational planning metadata and optimization records",
    )
    validation_errors: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
        comment="Validation errors if plan validation failed",
    )
    version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
        comment="Incremental version number of the plan",
    )

    # Relationships
    workflow: Mapped["Workflow | None"] = relationship(
        "Workflow",
        back_populates="execution_plans",
        lazy="selectin",
    )
    iterations: Mapped[list["PlanningIterationModel"]] = relationship(
        "PlanningIterationModel",
        back_populates="plan",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="PlanningIterationModel.iteration_number",
    )


class PlanningIterationModel(Base, UUIDPrimaryKeyMixin):
    """Database persistence model for intermediate planning and repair iterations."""

    __tablename__ = "planning_iterations"

    plan_id: Mapped[uuid.UUID] = mapped_column(
        types.Uuid(as_uuid=True),
        ForeignKey("execution_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Foreign key referencing the parent execution plan",
    )
    iteration_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="1-based sequence index of the planning iteration attempt",
    )
    prompt: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Instruction prompt given to the planner agent for this attempt",
    )
    raw_plan_data: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
        comment="Raw structured response received from the planner agent",
    )
    validation_passed: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="Whether the plan passed validation in this iteration",
    )
    validation_errors: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
        comment="Validation errors identified during this iteration",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        comment="UTC timestamp when this iteration was executed",
    )

    # Relationships
    plan: Mapped["ExecutionPlanModel"] = relationship(
        "ExecutionPlanModel",
        back_populates="iterations",
        lazy="selectin",
    )
