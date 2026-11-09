"""Initial migration for workflows and tasks tables.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-07 21:46:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create workflows and tasks tables with indexes and foreign keys."""
    # 1. Create workflows table
    op.create_table(
        "workflows",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            nullable=False,
            comment="UUID primary key",
        ),
        sa.Column(
            "name",
            sa.String(length=255),
            nullable=False,
            comment="Human-readable workflow name",
        ),
        sa.Column(
            "description",
            sa.Text(),
            nullable=True,
            comment="Detailed workflow description or user objective",
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="PENDING",
            comment="Current execution lifecycle status",
        ),
        sa.Column(
            "input_data",
            sa.JSON(),
            nullable=False,
            comment="Input parameters and configuration",
        ),
        sa.Column(
            "result_data",
            sa.JSON(),
            nullable=True,
            comment="Final output deliverable or error details upon completion",
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_workflows_name"), "workflows", ["name"], unique=False)
    op.create_index(op.f("ix_workflows_status"), "workflows", ["status"], unique=False)
    op.create_index(op.f("ix_workflows_created_at"), "workflows", ["created_at"], unique=False)

    # 2. Create tasks table
    op.create_table(
        "tasks",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            nullable=False,
            comment="UUID primary key",
        ),
        sa.Column(
            "workflow_id",
            sa.Uuid(as_uuid=True),
            nullable=False,
            comment="Foreign key referencing the parent workflow",
        ),
        sa.Column(
            "name",
            sa.String(length=255),
            nullable=False,
            comment="Human-readable subtask name",
        ),
        sa.Column(
            "description",
            sa.Text(),
            nullable=True,
            comment="Specific instructions or parameters for this subtask",
        ),
        sa.Column(
            "task_type",
            sa.String(length=100),
            nullable=False,
            server_default="generic",
            comment="Agent specialization required",
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="PENDING",
            comment="Current execution lifecycle status",
        ),
        sa.Column(
            "input_data",
            sa.JSON(),
            nullable=False,
            comment="Input payload required for task execution",
        ),
        sa.Column(
            "result_data",
            sa.JSON(),
            nullable=True,
            comment="Output payload produced by task execution",
        ),
        sa.Column(
            "error_message",
            sa.Text(),
            nullable=True,
            comment="Error trace or failure reason if status is FAILED",
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
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tasks_workflow_id"), "tasks", ["workflow_id"], unique=False)
    op.create_index(op.f("ix_tasks_name"), "tasks", ["name"], unique=False)
    op.create_index(op.f("ix_tasks_task_type"), "tasks", ["task_type"], unique=False)
    op.create_index(op.f("ix_tasks_status"), "tasks", ["status"], unique=False)
    op.create_index(op.f("ix_tasks_created_at"), "tasks", ["created_at"], unique=False)


def downgrade() -> None:
    """Drop tasks and workflows tables in reverse order."""
    op.drop_index(op.f("ix_tasks_created_at"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_status"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_task_type"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_name"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_workflow_id"), table_name="tasks")
    op.drop_table("tasks")

    op.drop_index(op.f("ix_workflows_created_at"), table_name="workflows")
    op.drop_index(op.f("ix_workflows_status"), table_name="workflows")
    op.drop_index(op.f("ix_workflows_name"), table_name="workflows")
    op.drop_table("workflows")
