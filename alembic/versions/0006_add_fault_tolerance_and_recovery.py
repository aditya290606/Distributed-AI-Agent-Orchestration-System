"""Add task lease, worker nodes registry, and recovery audit history.

Revision ID: 0006_add_fault_tolerance_and_recovery
Revises: 0005_add_celery_task_fields
Create Date: 2026-11-09 17:15:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_add_fault_tolerance_and_recovery"
down_revision: str | None = "0005_add_celery_task_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply schema updates for task leasing, worker heartbeat monitoring, and recovery audit."""
    # 1. Extend tasks table with lease tracking and error classifications
    op.add_column(
        "tasks",
        sa.Column(
            "lease_token",
            sa.String(length=64),
            nullable=True,
            comment="Cryptographic token identifying active lease ownership",
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "lease_acquired_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="UTC timestamp when the lease was claimed",
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "lease_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="UTC timestamp when current lease validity expires",
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "lease_renewed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="UTC timestamp when lease was most recently renewed",
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "recovery_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Number of times this task was automatically recovered from failure",
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "last_error_category",
            sa.String(length=64),
            nullable=True,
            comment="Classification of the most recent error (TRANSIENT, PERMANENT, TIMEOUT)",
        ),
    )

    op.create_index("ix_tasks_lease_token", "tasks", ["lease_token"], unique=False)
    op.create_index("ix_tasks_lease_expires_at", "tasks", ["lease_expires_at"], unique=False)

    # 2. Create worker_nodes table for persistent heartbeat and status tracking
    op.create_table(
        "worker_nodes",
        sa.Column(
            "worker_id",
            sa.String(length=255),
            primary_key=True,
            nullable=False,
            comment="Unique Celery worker node name",
        ),
        sa.Column(
            "hostname",
            sa.String(length=255),
            nullable=True,
            comment="Worker host machine name or IP",
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="ONLINE",
            comment="Worker operational status (ONLINE, SUSPECTED, OFFLINE)",
        ),
        sa.Column(
            "last_heartbeat",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="UTC timestamp of the most recent heartbeat signal",
        ),
        sa.Column(
            "registered_queues",
            sa.JSON(),
            nullable=False,
            comment="List of Celery queues consumed by this worker node",
        ),
        sa.Column(
            "active_task_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Number of tasks currently being processed by this worker",
        ),
        sa.Column(
            "startup_time",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="UTC timestamp when the worker node initialized",
        ),
        sa.Column(
            "shutdown_time",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="UTC timestamp when the worker gracefully deregistered",
        ),
        sa.Column(
            "worker_metadata",
            sa.JSON(),
            nullable=False,
            comment="Arbitrary worker specifications",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )
    op.create_index("ix_worker_nodes_status", "worker_nodes", ["status"], unique=False)
    op.create_index(
        "ix_worker_nodes_last_heartbeat", "worker_nodes", ["last_heartbeat"], unique=False
    )

    # 3. Create recovery_events table for immutable audit history
    op.create_table(
        "recovery_events",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "workflow_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=False,
            comment="Foreign key referencing the affected workflow",
        ),
        sa.Column(
            "task_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
            comment="Foreign key referencing the recovered task",
        ),
        sa.Column(
            "previous_status",
            sa.String(length=32),
            nullable=False,
            comment="Task status prior to recovery intervention",
        ),
        sa.Column(
            "new_status",
            sa.String(length=32),
            nullable=False,
            comment="Task status after recovery action was applied",
        ),
        sa.Column(
            "recovery_reason",
            sa.String(length=255),
            nullable=False,
            comment="Cause triggering recovery",
        ),
        sa.Column(
            "recovery_action",
            sa.String(length=64),
            nullable=False,
            comment="Action taken (REQUEUE, RETRY, MARK_FAILED, MARK_CANCELLED, MANUAL_REVIEW)",
        ),
        sa.Column(
            "worker_id",
            sa.String(length=255),
            nullable=True,
            comment="Worker that held the task before recovery",
        ),
        sa.Column(
            "lease_token",
            sa.String(length=64),
            nullable=True,
            comment="Lease token that was invalidated or revoked",
        ),
        sa.Column(
            "retry_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Retry attempt count at the time of recovery",
        ),
        sa.Column(
            "error_category",
            sa.String(length=64),
            nullable=True,
            comment="Classification of the error",
        ),
        sa.Column(
            "event_metadata",
            sa.JSON(),
            nullable=False,
            comment="Detailed telemetry, error traces, or diagnostic payloads",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="UTC timestamp when the recovery event occurred",
        ),
    )
    op.create_index(
        "ix_recovery_events_workflow_id", "recovery_events", ["workflow_id"], unique=False
    )
    op.create_index("ix_recovery_events_task_id", "recovery_events", ["task_id"], unique=False)
    op.create_index(
        "ix_recovery_events_recovery_action", "recovery_events", ["recovery_action"], unique=False
    )
    op.create_index(
        "ix_recovery_events_created_at", "recovery_events", ["created_at"], unique=False
    )


def downgrade() -> None:
    """Revert schema changes."""
    op.drop_index("ix_recovery_events_created_at", table_name="recovery_events")
    op.drop_index("ix_recovery_events_recovery_action", table_name="recovery_events")
    op.drop_index("ix_recovery_events_task_id", table_name="recovery_events")
    op.drop_index("ix_recovery_events_workflow_id", table_name="recovery_events")
    op.drop_table("recovery_events")

    op.drop_index("ix_worker_nodes_last_heartbeat", table_name="worker_nodes")
    op.drop_index("ix_worker_nodes_status", table_name="worker_nodes")
    op.drop_table("worker_nodes")

    op.drop_index("ix_tasks_lease_expires_at", table_name="tasks")
    op.drop_index("ix_tasks_lease_token", table_name="tasks")
    op.drop_column("tasks", "last_error_category")
    op.drop_column("tasks", "recovery_count")
    op.drop_column("tasks", "lease_renewed_at")
    op.drop_column("tasks", "lease_expires_at")
    op.drop_column("tasks", "lease_acquired_at")
    op.drop_column("tasks", "lease_token")
