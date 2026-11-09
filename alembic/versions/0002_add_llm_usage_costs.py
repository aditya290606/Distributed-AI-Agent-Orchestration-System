"""Add llm_usage_costs table for token tracking and cost accounting.

Revision ID: 0002_add_llm_usage_costs
Revises: 0001_initial
Create Date: 2026-11-07 22:23:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_add_llm_usage_costs"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create llm_usage_costs table."""
    op.create_table(
        "llm_usage_costs",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            nullable=False,
            comment="UUID primary key",
        ),
        sa.Column(
            "workflow_id",
            sa.Uuid(as_uuid=True),
            nullable=True,
            comment="Optional foreign key to associated workflow",
        ),
        sa.Column(
            "task_id",
            sa.Uuid(as_uuid=True),
            nullable=True,
            comment="Optional foreign key to associated subtask",
        ),
        sa.Column(
            "model_name",
            sa.String(length=100),
            nullable=False,
            comment="Full model identifier used for inference",
        ),
        sa.Column(
            "prompt_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Number of input prompt tokens",
        ),
        sa.Column(
            "completion_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Number of generated output tokens",
        ),
        sa.Column(
            "total_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Total token sum (prompt + completion)",
        ),
        sa.Column(
            "duration_ms",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Round-trip request duration in milliseconds",
        ),
        sa.Column(
            "is_success",
            sa.Boolean(),
            nullable=False,
            server_default="1",
            comment="Whether the LLM call succeeded",
        ),
        sa.Column(
            "error_type",
            sa.String(length=100),
            nullable=True,
            comment="Error classification code if call failed",
        ),
        sa.Column(
            "estimated_cost_usd",
            sa.Numeric(precision=10, scale=6),
            nullable=True,
            comment="Estimated cost in USD based on official model pricing",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="UTC creation timestamp",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="UTC last update timestamp",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_llm_usage_costs_workflow_id"), "llm_usage_costs", ["workflow_id"], unique=False
    )
    op.create_index(
        op.f("ix_llm_usage_costs_task_id"), "llm_usage_costs", ["task_id"], unique=False
    )
    op.create_index(
        op.f("ix_llm_usage_costs_model_name"), "llm_usage_costs", ["model_name"], unique=False
    )


def downgrade() -> None:
    """Drop llm_usage_costs table."""
    op.drop_index(op.f("ix_llm_usage_costs_model_name"), table_name="llm_usage_costs")
    op.drop_index(op.f("ix_llm_usage_costs_task_id"), table_name="llm_usage_costs")
    op.drop_index(op.f("ix_llm_usage_costs_workflow_id"), table_name="llm_usage_costs")
    op.drop_table("llm_usage_costs")
