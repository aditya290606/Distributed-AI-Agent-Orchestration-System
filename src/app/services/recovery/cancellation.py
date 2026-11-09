"""Safe workflow and task cancellation service."""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.recovery import RecoveryEventModel
from app.db.models.task import Task, TaskStatus
from app.db.models.workflow import Workflow, WorkflowStatus
from app.workers.celery_app import celery_app

logger = logging.getLogger("app.services.recovery.cancellation")


class WorkflowCancellationService:
    """Coordinates graceful and forced cancellation of active workflows and tasks."""

    async def cancel_workflow(
        self,
        workflow_id: uuid.UUID,
        session: AsyncSession,
        reason: str = "User requested cancellation",
    ) -> dict[str, Any]:
        """Safely cancel an active workflow, revoking Celery tasks and preserving completed deliverables.

        Args:
            workflow_id: Workflow UUID to cancel.
            session: Active database session.
            reason: Cancellation justification.

        Returns:
            Dictionary summarizing cancelled and preserved tasks.
        """
        wf_q = select(Workflow).where(Workflow.id == workflow_id)
        wf_res = await session.execute(wf_q)
        workflow = wf_res.scalar_one_or_none()
        if not workflow:
            raise ValueError(f"Workflow '{workflow_id}' does not exist.")

        if workflow.status in (WorkflowStatus.COMPLETED.value, WorkflowStatus.CANCELLED.value):
            return {
                "workflow_id": str(workflow_id),
                "status": workflow.status,
                "message": f"Workflow is already in terminal state '{workflow.status}'.",
                "cancelled_tasks_count": 0,
            }

        workflow.status = WorkflowStatus.CANCELLED.value

        # Fetch all tasks
        tasks_q = select(Task).where(Task.workflow_id == workflow_id)
        tasks_res = await session.execute(tasks_q)
        all_tasks = list(tasks_res.scalars().all())

        cancelled_count = 0
        preserved_count = 0

        for task in all_tasks:
            if task.status in (TaskStatus.SUCCEEDED.value, TaskStatus.COMPLETED.value):
                preserved_count += 1
                continue

            prev_status = task.status
            prev_token = task.lease_token
            prev_worker = task.worker_id

            # Revoke in Celery
            if task.celery_task_id:
                try:
                    celery_app.control.revoke(task.celery_task_id, terminate=True)
                    logger.info(
                        "Revoked Celery task '%s' for cancelled task '%s'",
                        task.celery_task_id,
                        task.id,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to revoke Celery task '%s': %s", task.celery_task_id, exc
                    )

            task.status = TaskStatus.CANCELLED.value
            task.error_message = reason
            task.lease_token = None
            task.lease_expires_at = None
            cancelled_count += 1

            # Audit event
            event = RecoveryEventModel(
                id=uuid.uuid4(),
                workflow_id=workflow_id,
                task_id=task.id,
                previous_status=prev_status,
                new_status=TaskStatus.CANCELLED.value,
                recovery_reason=reason[:255],
                recovery_action="MARK_CANCELLED",
                worker_id=prev_worker,
                lease_token=prev_token,
                retry_count=task.retry_count,
                error_category="CANCELLED",
                event_metadata={"planned_task_id": task.planned_task_id},
                created_at=datetime.now(UTC),
            )
            session.add(event)

        await session.commit()
        logger.info(
            "Workflow '%s' cancelled. %d tasks cancelled, %d preserved.",
            workflow_id,
            cancelled_count,
            preserved_count,
        )

        return {
            "workflow_id": str(workflow_id),
            "status": WorkflowStatus.CANCELLED.value,
            "cancelled_tasks_count": cancelled_count,
            "preserved_tasks_count": preserved_count,
            "reason": reason,
        }
