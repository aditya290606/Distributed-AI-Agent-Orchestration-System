"""Add execution_plans and planning_iterations tables for dynamic planning.

Revision ID: 0004_add_execution_plans_and_iterations
Revises: 0003_add_agent_executions
Create Date: 2026-11-08 19:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_add_execution_plans_and_iterations"
down_revision: str | None = "0003_add_agent_executions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create execution_plans and planning_iterations tables."""
    # 1. execution_plans
    op.create_table(
        "execution_plans",
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
            comment="Optional foreign key referencing associated parent workflow",
        ),
        sa.Column(
            "objective",
            sa.Text(),
            nullable=False,
            comment="Normalized objective of the execution plan",
        ),
        sa.Column(
            "plan_status",
            sa.String(length=32),
            nullable=False,
            server_default="DRAFT",
            comment="Lifecycle validation status (DRAFT, VALIDATED, REVISED, OPTIMIZED, REJECTED, etc.)",
        ),
        sa.Column(
            "task_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Total count of planned subtasks",
        ),
        sa.Column(
            "tasks_data",
            sa.JSON(),
            nullable=False,
            comment="Complete JSON serialization of planned tasks",
        ),
        sa.Column(
            "stages_data",
            sa.JSON(),
            nullable=False,
            comment="JSON serialization of parallel execution stages",
        ),
        sa.Column(
            "plan_metadata",
            sa.JSON(),
            nullable=False,
            comment="Operational planning metadata and optimization records",
        ),
        sa.Column(
            "validation_errors",
            sa.JSON(),
            nullable=False,
            comment="Validation errors if plan validation failed",
        ),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default="1",
            comment="Incremental version number of the plan",
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
            name="fk_execution_plans_workflow_id",
            ondelete="CASCADE",
        ),
    )

    op.create_index(
        "ix_execution_plans_workflow_id",
        "execution_plans",
        ["workflow_id"],
        unique=False,
    )
    op.create_index(
        "ix_execution_plans_plan_status",
        "execution_plans",
        ["plan_status"],
        unique=False,
    )

    # 2. planning_iterations
    op.create_table(
        "planning_iterations",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            nullable=False,
            primary_key=True,
            comment="UUID primary key",
        ),
        sa.Column(
            "plan_id",
            sa.Uuid(as_uuid=True),
            nullable=False,
            comment="Foreign key referencing the parent execution plan",
        ),
        sa.Column(
            "iteration_number",
            sa.Integer(),
            nullable=False,
            comment="1-based sequence index of the planning iteration attempt",
        ),
        sa.Column(
            "prompt",
            sa.Text(),
            nullable=True,
            comment="Instruction prompt given to the planner agent for this attempt",
        ),
        sa.Column(
            "raw_plan_data",
            sa.JSON(),
            nullable=False,
            comment="Raw structured response received from the planner agent",
        ),
        sa.Column(
            "validation_passed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment="Whether the plan passed validation in this iteration",
        ),
        sa.Column(
            "validation_errors",
            sa.JSON(),
            nullable=False,
            comment="Validation errors identified during this iteration",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="UTC timestamp when this iteration was executed",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["execution_plans.id"],
            name="fk_planning_iterations_plan_id",
            ondelete="CASCADE",
        ),
    )

    op.create_index(
        "ix_planning_iterations_plan_id",
        "planning_iterations",
        ["plan_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop planning_iterations and execution_plans tables."""
    op.drop_index("ix_planning_iterations_plan_id", table_name="planning_iterations")
    op.drop_table("planning_iterations")

    op.drop_index("ix_execution_plans_plan_status", table_name="execution_plans")
    op.drop_index("ix_execution_plans_workflow_id", table_name="execution_plans")
    op.drop_table("execution_plans")
