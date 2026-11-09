"""Add agent_executions table for specialized agent audit and tracking.

Revision ID: 0003_add_agent_executions
Revises: 0002_add_llm_usage_costs
Create Date: 2026-11-08 17:55:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_add_agent_executions"
down_revision: str | None = "0002_add_llm_usage_costs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create agent_executions table."""
    op.create_table(
        "agent_executions",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            nullable=False,
            primary_key=True,
            comment="UUID primary key",
        ),
        sa.Column(
            "workflow_id",
            sa.Uuid(as_uuid=True),
            nullable=True,
            comment="Optional foreign key to associated parent workflow",
        ),
        sa.Column(
            "task_id",
            sa.Uuid(as_uuid=True),
            nullable=True,
            comment="Optional foreign key to associated subtask",
        ),
        sa.Column(
            "agent_id",
            sa.String(length=50),
            nullable=False,
            comment="Unique identifier of the executed agent",
        ),
        sa.Column(
            "agent_name",
            sa.String(length=100),
            nullable=False,
            comment="Human-readable name of the executed agent",
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            comment="Terminal execution status (SUCCESS, FAILED, PARTIAL_SUCCESS, SKIPPED)",
        ),
        sa.Column(
            "input_summary",
            sa.JSON(),
            nullable=False,
            comment="Sanitized summary of task description and input context",
        ),
        sa.Column(
            "output_summary",
            sa.JSON(),
            nullable=False,
            comment="Structured output deliverable payload",
        ),
        sa.Column(
            "reasoning_summary",
            sa.Text(),
            nullable=True,
            comment="High-level reasoning summary detailing conclusions and decisions only",
        ),
        sa.Column(
            "error_message",
            sa.Text(),
            nullable=True,
            comment="Error message if execution failed, else None",
        ),
        sa.Column(
            "duration_ms",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Total round-trip execution duration in milliseconds",
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="UTC timestamp when agent execution started",
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="UTC timestamp when agent execution completed",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="UTC timestamp when record was created",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="UTC timestamp when record was last updated",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
            name="fk_agent_executions_workflow_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            name="fk_agent_executions_task_id",
            ondelete="SET NULL",
        ),
    )

    op.create_index(
        "ix_agent_executions_agent_id",
        "agent_executions",
        ["agent_id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_executions_status",
        "agent_executions",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_agent_executions_workflow_id",
        "agent_executions",
        ["workflow_id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_executions_task_id",
        "agent_executions",
        ["task_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop agent_executions table."""
    op.drop_index("ix_agent_executions_task_id", table_name="agent_executions")
    op.drop_index("ix_agent_executions_workflow_id", table_name="agent_executions")
    op.drop_index("ix_agent_executions_status", table_name="agent_executions")
    op.drop_index("ix_agent_executions_agent_id", table_name="agent_executions")
    op.drop_table("agent_executions")
