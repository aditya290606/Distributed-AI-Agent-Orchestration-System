"""Database models for worker nodes, heartbeats, and recovery audit history."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, types
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class WorkerNodeModel(Base, TimestampMixin):
    """Represents a registered Celery worker node in the distributed cluster."""

    __tablename__ = "worker_nodes"

    worker_id: Mapped[str] = mapped_column(
        String(255),
        primary_key=True,
        comment="Unique Celery worker node name (e.g. celery@worker-1)",
    )
    hostname: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Worker host machine name or IP",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="ONLINE",
        nullable=False,
        index=True,
        comment="Worker operational status (ONLINE, SUSPECTED, OFFLINE)",
    )
    last_heartbeat: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
        comment="UTC timestamp of the most recent heartbeat signal",
    )
    registered_queues: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
        comment="List of Celery queues consumed by this worker node",
    )
    active_task_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="Number of tasks currently being processed by this worker",
    )
    startup_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="UTC timestamp when the worker node initialized",
    )
    shutdown_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="UTC timestamp when the worker gracefully deregistered",
    )
    worker_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
        comment="Arbitrary worker specifications (concurrency, pool type, pid)",
    )


class RecoveryEventModel(Base, UUIDPrimaryKeyMixin):
    """Immutable audit trail of all automated and manual fault recovery actions."""

    __tablename__ = "recovery_events"

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        types.Uuid(as_uuid=True),
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Foreign key referencing the affected workflow",
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        types.Uuid(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Foreign key referencing the recovered task",
    )
    previous_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="Task status prior to recovery intervention",
    )
    new_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="Task status after recovery action was applied",
    )
    recovery_reason: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Cause triggering recovery (e.g. LEASE_EXPIRED, WORKER_OFFLINE, TIMEOUT)",
    )
    recovery_action: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment="Action taken (REQUEUE, RETRY, MARK_FAILED, MARK_CANCELLED, MANUAL_REVIEW)",
    )
    worker_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Worker that held the task before recovery",
    )
    lease_token: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="Lease token that was invalidated or revoked",
    )
    retry_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="Retry attempt count at the time of recovery",
    )
    error_category: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="Classification of the error (TRANSIENT, PERMANENT, TIMEOUT)",
    )
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
        comment="Detailed telemetry, error traces, or diagnostic payloads",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
        comment="UTC timestamp when the recovery event occurred",
    )
