"""Add Celery distributed execution and worker fields to tasks table.

Revision ID: 0005_add_celery_task_fields
Revises: 0004_add_execution_plans_and_iterations
Create Date: 2026-11-08 19:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_add_celery_task_fields"
down_revision: str | None = "0004_add_execution_plans_and_iterations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add distributed task tracking columns to tasks table."""
    op.add_column(
        "tasks",
        sa.Column(
            "plan_id",
            sa.Uuid(as_uuid=True),
            nullable=True,
            comment="Optional foreign key referencing associated execution plan",
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "planned_task_id",
            sa.String(length=100),
            nullable=True,
            comment="Planned task string identifier from execution plan",
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "celery_task_id",
            sa.String(length=255),
            nullable=True,
            comment="Celery asynchronous task UUID string",
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "worker_id",
            sa.String(length=255),
            nullable=True,
            comment="Worker identifier or hostname that processed the task",
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "retry_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Number of times execution has been retried",
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="UTC timestamp when worker execution began",
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="UTC timestamp when execution concluded",
        ),
    )

    op.create_foreign_key(
        "fk_tasks_plan_id",
        "tasks",
        "execution_plans",
        ["plan_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_tasks_plan_id", "tasks", ["plan_id"], unique=False)
    op.create_index("ix_tasks_planned_task_id", "tasks", ["planned_task_id"], unique=False)
    op.create_index("ix_tasks_celery_task_id", "tasks", ["celery_task_id"], unique=False)


def downgrade() -> None:
    """Remove distributed task tracking columns from tasks table."""
    op.drop_index("ix_tasks_celery_task_id", table_name="tasks")
    op.drop_index("ix_tasks_planned_task_id", table_name="tasks")
    op.drop_index("ix_tasks_plan_id", table_name="tasks")
    op.drop_constraint("fk_tasks_plan_id", "tasks", type_="foreignkey")

    op.drop_column("tasks", "completed_at")
    op.drop_column("tasks", "started_at")
    op.drop_column("tasks", "retry_count")
    op.drop_column("tasks", "worker_id")
    op.drop_column("tasks", "celery_task_id")
    op.drop_column("tasks", "planned_task_id")
    op.drop_column("tasks", "plan_id")
