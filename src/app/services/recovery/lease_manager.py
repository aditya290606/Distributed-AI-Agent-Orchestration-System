"""Database-backed atomic task lease and distributed ownership manager."""

import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.task import Task, TaskStatus

logger = logging.getLogger("app.services.recovery.lease_manager")


class TaskLeaseManager:
    """Coordinates atomic task leasing, heartbeat renewals, and lease completion validation."""

    def __init__(self, default_lease_duration_seconds: int = 300) -> None:
        """Initialize lease manager.

        Args:
            default_lease_duration_seconds: Default lease TTL in seconds (default: 5 minutes).
        """
        self.default_lease_duration = default_lease_duration_seconds

    async def acquire_lease(
        self,
        task_id: uuid.UUID,
        worker_id: str,
        session: AsyncSession,
        duration_seconds: int | None = None,
    ) -> str | None:
        """Atomically acquire an exclusive execution lease on a task.

        Guarantees that only one worker can hold an active lease. Eligible if:
        1. Task is in PENDING, QUEUED, or RETRYING status, OR
        2. Task is in RUNNING status but its previous lease has expired.

        Args:
            task_id: Unique UUID of the task.
            worker_id: Hostname or ID of the acquiring worker process.
            session: Active database session.
            duration_seconds: Optional TTL override in seconds.

        Returns:
            Lease token string on successful acquisition, or None if task is already leased.
        """
        ttl = duration_seconds if duration_seconds is not None else self.default_lease_duration
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=ttl)
        lease_token = secrets.token_hex(16)

        # Atomic conditional update
        stmt = (
            update(Task)
            .where(
                Task.id == task_id,
                or_(
                    Task.status.in_(
                        [
                            TaskStatus.PENDING.value,
                            TaskStatus.QUEUED.value,
                            TaskStatus.RETRYING.value,
                        ]
                    ),
                    and_(
                        Task.status == TaskStatus.RUNNING.value,
                        or_(Task.lease_expires_at.is_(None), Task.lease_expires_at < now),
                    ),
                ),
            )
            .values(
                status=TaskStatus.RUNNING.value,
                worker_id=worker_id,
                lease_token=lease_token,
                lease_acquired_at=now,
                lease_expires_at=expires_at,
                lease_renewed_at=now,
                started_at=func.coalesce(Task.started_at, now),
            )
        )
        result = await session.execute(stmt)
        if result.rowcount > 0:
            logger.info(
                "Worker '%s' acquired lease on task '%s' (token: %s, ttl: %ds)",
                worker_id,
                task_id,
                lease_token[:8] + "...",
                ttl,
            )
            return lease_token

        logger.warning(
            "Worker '%s' failed to acquire lease on task '%s' (already leased or non-claimable)",
            worker_id,
            task_id,
        )
        return None

    async def renew_lease(
        self,
        task_id: uuid.UUID,
        lease_token: str,
        session: AsyncSession,
        extension_seconds: int | None = None,
    ) -> bool:
        """Atomically renew an active lease, verifying token and non-expired state.

        Args:
            task_id: Task UUID.
            lease_token: Ownership token provided upon acquisition.
            session: Active database session.
            extension_seconds: Optional extension duration in seconds.

        Returns:
            True if renewed, False if lease was revoked, expired, or reassigned.
        """
        ttl = extension_seconds if extension_seconds is not None else self.default_lease_duration
        now = datetime.now(UTC)
        new_expires_at = now + timedelta(seconds=ttl)

        stmt = (
            update(Task)
            .where(
                Task.id == task_id,
                Task.lease_token == lease_token,
                Task.status == TaskStatus.RUNNING.value,
                Task.lease_expires_at >= now,
            )
            .values(
                lease_expires_at=new_expires_at,
                lease_renewed_at=now,
            )
        )
        result = await session.execute(stmt)
        success = result.rowcount > 0
        if success:
            logger.debug("Renewed lease on task '%s' for %ds", task_id, ttl)
        else:
            logger.warning(
                "Lease renewal rejected for task '%s' (token mismatch or lease expired)", task_id
            )
        return success

    async def release_lease(
        self,
        task_id: uuid.UUID,
        lease_token: str,
        target_status: TaskStatus,
        session: AsyncSession,
        result_data: dict | None = None,
        error_message: str | None = None,
    ) -> bool:
        """Atomically persist final task execution outcome and release lease.

        Strictly verifies that the committing worker is still the valid lease holder.
        Prevents zombie workers whose leases expired from overwriting valid work.

        Args:
            task_id: Task UUID.
            lease_token: Token acquired before execution.
            target_status: Terminal status (SUCCEEDED, FAILED, CANCELLED).
            session: Active database session.
            result_data: Optional output payload.
            error_message: Optional error diagnostics.

        Returns:
            True if release succeeded, False if ownership was already lost.
        """
        now = datetime.now(UTC)
        stmt = (
            update(Task)
            .where(
                Task.id == task_id,
                Task.lease_token == lease_token,
                Task.status == TaskStatus.RUNNING.value,
            )
            .values(
                status=target_status.value,
                result_data=result_data,
                error_message=error_message,
                completed_at=now,
                lease_token=None,
                lease_expires_at=None,
            )
        )
        result = await session.execute(stmt)
        success = result.rowcount > 0
        if success:
            logger.info(
                "Released lease on task '%s' with status '%s'", task_id, target_status.value
            )
        else:
            logger.error(
                "CRITICAL: Worker attempted to complete task '%s' with status '%s', but no longer holds valid lease!",
                task_id,
                target_status.value,
            )
        return success

    async def is_lease_valid(
        self,
        task_id: uuid.UUID,
        lease_token: str,
        session: AsyncSession,
    ) -> bool:
        """Check whether the given lease token currently holds active valid ownership."""
        now = datetime.now(UTC)
        query = select(Task.id).where(
            Task.id == task_id,
            Task.lease_token == lease_token,
            Task.status == TaskStatus.RUNNING.value,
            Task.lease_expires_at >= now,
        )
        res = await session.execute(query)
        return res.scalar_one_or_none() is not None
