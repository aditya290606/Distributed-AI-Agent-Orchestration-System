"""LLM usage and cost tracking database model."""

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, types
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.task import Task
    from app.db.models.workflow import Workflow


class LLMUsageCost(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Tracks token consumption, latency, and financial cost per LLM invocation."""

    __tablename__ = "llm_usage_costs"

    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        types.Uuid(as_uuid=True),
        ForeignKey("workflows.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Optional foreign key to associated workflow",
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        types.Uuid(as_uuid=True),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Optional foreign key to associated subtask",
    )
    model_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment="Full model identifier used for inference",
    )
    prompt_tokens: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="Number of input prompt tokens",
    )
    completion_tokens: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="Number of generated output tokens",
    )
    total_tokens: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="Total token sum (prompt + completion)",
    )
    duration_ms: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="Round-trip request duration in milliseconds",
    )
    is_success: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        comment="Whether the LLM call succeeded",
    )
    error_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Error classification code if call failed",
    )
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 6),
        nullable=True,
        comment="Estimated cost in USD based on official model pricing",
    )

    # Relationships
    workflow: Mapped["Workflow | None"] = relationship("Workflow", lazy="selectin")
    task: Mapped["Task | None"] = relationship("Task", lazy="selectin")
