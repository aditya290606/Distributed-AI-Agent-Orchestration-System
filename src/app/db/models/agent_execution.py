"""Agent execution persistence database model."""

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, types
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.task import Task
    from app.db.models.workflow import Workflow


class AgentExecution(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Tracks individual agent execution runs, lifecycle status, deliverables, and metrics."""

    __tablename__ = "agent_executions"

    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        types.Uuid(as_uuid=True),
        ForeignKey("workflows.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Optional foreign key to associated parent workflow",
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        types.Uuid(as_uuid=True),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Optional foreign key to associated subtask",
    )
    agent_id: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
        comment="Unique identifier of the executed agent (e.g. 'supervisor', 'coder')",
    )
    agent_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Human-readable name of the executed agent",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
        comment="Terminal execution status (SUCCESS, FAILED, PARTIAL_SUCCESS, SKIPPED)",
    )
    input_summary: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        comment="Sanitized summary of task description and input context",
    )
    output_summary: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        comment="Structured output deliverable payload",
    )
    reasoning_summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="High-level reasoning summary detailing conclusions and decisions only",
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Error message if execution failed, else None",
    )
    duration_ms: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="Total round-trip execution duration in milliseconds",
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        comment="UTC timestamp when agent execution started",
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        comment="UTC timestamp when agent execution completed",
    )

    # ORM Relationships
    workflow: Mapped["Workflow | None"] = relationship(
        "Workflow",
        backref="agent_executions",
        lazy="selectin",
    )
    task: Mapped["Task | None"] = relationship(
        "Task",
        backref="agent_executions",
        lazy="selectin",
    )
