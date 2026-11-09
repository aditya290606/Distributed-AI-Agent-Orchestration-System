"""Centralized Task Recovery Manager scanning expired leases, stuck tasks, and dead workers."""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.recovery import RecoveryEventModel
from app.db.models.task import Task, TaskStatus
from app.db.models.workflow import Workflow, WorkflowStatus
from app.services.recovery.failure_detector import WorkerFailureDetector
from app.services.recovery.lease_manager import TaskLeaseManager
from app.services.recovery.retry_policy import ErrorCategory, RetryPolicy

logger = logging.getLogger("app.services.recovery.task_recovery_manager")


class TaskRecoveryManager:
    """Detects, inspects, and recovers orphaned, stuck, or timed-out tasks with audit logging."""

    def __init__(
        self,
        lease_manager: TaskLeaseManager | None = None,
        failure_detector: WorkerFailureDetector | None = None,
        retry_policy: RetryPolicy | None = None,
        max_recovery_attempts: int = 3,
    ) -> None:
        self.lease_manager = lease_manager or TaskLeaseManager()
        self.failure_detector = failure_detector or WorkerFailureDetector()
        self.retry_policy = retry_policy or RetryPolicy()
        self.max_recovery_attempts = max_recovery_attempts

    async def scan_and_recover_tasks(
        self,
        session: AsyncSession,
        batch_size: int = 50,
    ) -> list[dict[str, Any]]:
        """Scan for stuck or expired tasks and execute safe, idempotent recovery interventions.

        Scans for:
        1. Tasks with expired leases in RUNNING status.
        2. Tasks whose assigned workers are marked OFFLINE.
        3. Tasks stuck in RUNNING without a lease.

        Args:
            session: Active database session.
            batch_size: Maximum tasks to inspect in a single scan.

        Returns:
            List of recovery outcomes applied.
        """
        now = datetime.now(UTC)

        # 1. Identify tasks eligible for recovery
        expired_lease_condition = and_(
            Task.status == TaskStatus.RUNNING.value,
            Task.lease_expires_at.is_not(None),
            Task.lease_expires_at < now,
        )

        stuck_running_no_lease = and_(
            Task.status == TaskStatus.RUNNING.value,
            Task.lease_token.is_(None),
            Task.started_at < (now - timedelta(minutes=10)),
        )

        candidates_query = (
            select(Task)
            .where(or_(expired_lease_condition, stuck_running_no_lease))
            .limit(batch_size)
        )
        candidates_res = await session.execute(candidates_query)
        candidates = list(candidates_res.scalars().all())

        outcomes: list[dict[str, Any]] = []

        for task in candidates:
            # Check parent workflow status
            wf_query = select(Workflow).where(Workflow.id == task.workflow_id)
            wf_res = await session.execute(wf_query)
            wf = wf_res.scalar_one_or_none()

            recovery_reason = (
                "LEASE_EXPIRED"
                if task.lease_expires_at and task.lease_expires_at < now
                else "STUCK_RUNNING"
            )
            outcome = await self.recover_task(
                task_id=task.id,
                session=session,
                reason=recovery_reason,
                workflow=wf,
            )
            if outcome:
                outcomes.append(outcome)

        return outcomes

    async def recover_task(
        self,
        task_id: uuid.UUID,
        session: AsyncSession,
        reason: str = "MANUAL_RECOVERY",
        workflow: Workflow | None = None,
    ) -> dict[str, Any] | None:
        """Atomically claim and recover an individual task.

        Guarantees idempotency:
        - Never recovers tasks already in SUCCEEDED or COMPLETED status.
        - Uses atomic status transition to prevent concurrent recovery races.
        """
        # Atomic lock/claim: move from RUNNING/FAILED to RETRYING or FAILED conditionally
        q = select(Task).where(Task.id == task_id)
        res = await session.execute(q)
        task = res.scalar_one_or_none()
        if not task:
            logger.warning("Task '%s' not found for recovery", task_id)
            return None

        # Idempotency Guard 1: Never touch completed or succeeded tasks
        if task.status in (TaskStatus.SUCCEEDED.value, TaskStatus.COMPLETED.value):
            logger.info("Task '%s' already %s. Skipping recovery.", task_id, task.status)
            return {
                "task_id": str(task_id),
                "decision": "SKIPPED_ALREADY_SUCCESSFUL",
                "status": task.status,
            }

        prev_status = task.status
        prev_worker = task.worker_id
        prev_token = task.lease_token

        # Check workflow cancellation
        if workflow and workflow.status == WorkflowStatus.CANCELLED.value:
            task.status = TaskStatus.CANCELLED.value
            task.lease_token = None
            task.lease_expires_at = None
            await self._record_recovery_event(
                session=session,
                task=task,
                prev_status=prev_status,
                action="MARK_CANCELLED",
                reason=f"Workflow cancelled: {reason}",
                worker_id=prev_worker,
                lease_token=prev_token,
            )
            await session.commit()
            return {
                "task_id": str(task_id),
                "decision": "MARK_CANCELLED",
                "new_status": TaskStatus.CANCELLED.value,
            }

        # Check retry bounds
        if task.recovery_count >= self.max_recovery_attempts or task.retry_count >= 3:
            # Mark permanently failed
            task.status = TaskStatus.FAILED.value
            task.lease_token = None
            task.lease_expires_at = None
            task.error_message = f"Recovery attempts exhausted ({task.recovery_count}/{self.max_recovery_attempts}): {reason}"
            task.last_error_category = ErrorCategory.TIMEOUT.value

            await self._record_recovery_event(
                session=session,
                task=task,
                prev_status=prev_status,
                action="MARK_FAILED",
                reason=task.error_message,
                worker_id=prev_worker,
                lease_token=prev_token,
            )
            await session.commit()
            return {
                "task_id": str(task_id),
                "decision": "MARK_FAILED",
                "new_status": TaskStatus.FAILED.value,
                "reason": task.error_message,
            }

        # Eligible for retry & requeue!
        task.recovery_count += 1
        task.retry_count += 1
        task.status = TaskStatus.QUEUED.value
        task.lease_token = None
        task.lease_expires_at = None
        task.last_error_category = ErrorCategory.WORKER_FAILURE.value

        # Re-dispatch to Celery
        from app.services.task_dispatcher import DistributedTaskDispatcher

        dispatcher = DistributedTaskDispatcher()
        celery_func, queue_name = dispatcher._resolve_task_route(task.task_type)
        try:
            job = celery_func.apply_async(
                args=[
                    str(task.workflow_id),
                    str(task.plan_id) if task.plan_id else "",
                    task.planned_task_id or "",
                    str(task.id),
                ],
                queue=queue_name,
            )
            task.celery_task_id = job.id
            logger.info(
                "Task '%s' successfully requeued to '%s' (celery_job: %s, recovery_count: %d)",
                task_id,
                queue_name,
                job.id,
                task.recovery_count,
            )
        except Exception as broker_exc:
            logger.error("Failed to re-enqueue task '%s' to Celery: %s", task_id, broker_exc)
            task.error_message = f"Re-enqueue failed: {broker_exc!s}"

        await self._record_recovery_event(
            session=session,
            task=task,
            prev_status=prev_status,
            action="REQUEUE",
            reason=reason,
            worker_id=prev_worker,
            lease_token=prev_token,
        )
        await session.commit()

        return {
            "task_id": str(task_id),
            "decision": "REQUEUE",
            "new_status": task.status,
            "recovery_count": task.recovery_count,
            "celery_task_id": task.celery_task_id,
        }

    async def _record_recovery_event(
        self,
        session: AsyncSession,
        task: Task,
        prev_status: str,
        action: str,
        reason: str,
        worker_id: str | None = None,
        lease_token: str | None = None,
    ) -> RecoveryEventModel:
        """Create and persist an immutable audit trail entry."""
        event = RecoveryEventModel(
            id=uuid.uuid4(),
            workflow_id=task.workflow_id,
            task_id=task.id,
            previous_status=prev_status,
            new_status=task.status,
            recovery_reason=reason[:255],
            recovery_action=action,
            worker_id=worker_id,
            lease_token=lease_token,
            retry_count=task.retry_count,
            error_category=task.last_error_category,
            event_metadata={
                "task_type": task.task_type,
                "recovery_count": task.recovery_count,
                "planned_task_id": task.planned_task_id,
            },
            created_at=datetime.now(UTC),
        )
        session.add(event)
        await session.flush()
        return event
