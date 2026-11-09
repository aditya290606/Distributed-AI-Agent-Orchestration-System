"""Workflow resumption and DAG-aware state recovery service."""

import contextlib
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.execution_plan import ExecutionPlanModel
from app.db.models.task import Task, TaskStatus
from app.db.models.workflow import Workflow, WorkflowStatus
from app.services.task_dispatcher import DistributedTaskDispatcher

logger = logging.getLogger("app.services.recovery.workflow_recovery")


class WorkflowRecoveryService:
    """Safely recovers interrupted workflows, preserving successful deliverables and resuming DAG execution."""

    def __init__(self, dispatcher: DistributedTaskDispatcher | None = None) -> None:
        self.dispatcher = dispatcher or DistributedTaskDispatcher()

    async def resume_workflow(
        self,
        workflow_id: uuid.UUID,
        session: AsyncSession,
    ) -> dict[str, Any]:
        """Inspect and resume an interrupted workflow without repeating completed subtasks.

        Args:
            workflow_id: UUID of the workflow to resume.
            session: Active database session.

        Returns:
            Dictionary detailing preserved, resumed, and blocked tasks.
        """
        # 1. Load Workflow
        wf_q = select(Workflow).where(Workflow.id == workflow_id)
        wf_res = await session.execute(wf_q)
        workflow = wf_res.scalar_one_or_none()
        if not workflow:
            raise ValueError(f"Workflow '{workflow_id}' not found.")

        if workflow.status == WorkflowStatus.COMPLETED.value:
            return {
                "workflow_id": str(workflow_id),
                "status": "ALREADY_COMPLETED",
                "message": "Workflow has already completed successfully. No tasks need resumption.",
            }

        # 2. Fetch all existing Tasks
        tasks_q = select(Task).where(Task.workflow_id == workflow_id)
        tasks_res = await session.execute(tasks_q)
        existing_tasks = list(tasks_res.scalars().all())

        # Determine Plan ID
        plan_id = None
        if existing_tasks and existing_tasks[0].plan_id:
            plan_id = existing_tasks[0].plan_id
        elif workflow.input_data and "plan_id" in workflow.input_data:
            with contextlib.suppress(Exception):
                plan_id = uuid.UUID(workflow.input_data["plan_id"])

        if not plan_id:
            raise ValueError(f"No execution plan associated with workflow '{workflow_id}'.")

        # 3. Load Execution Plan
        plan_q = select(ExecutionPlanModel).where(ExecutionPlanModel.id == plan_id)
        plan_res = await session.execute(plan_q)
        plan_model = plan_res.scalar_one_or_none()
        if not plan_model or not plan_model.stages_data:
            raise ValueError(f"Execution plan '{plan_id}' has no stages data.")

        task_by_planned_id: dict[str, Task] = {
            t.planned_task_id: t for t in existing_tasks if t.planned_task_id
        }

        # 4. Analyze tasks status across stages
        preserved_tasks: list[str] = []
        resumed_tasks: list[str] = []
        blocked_tasks: list[str] = []

        all_stages_complete = True
        any_task_failed = False

        for stage in plan_model.stages_data:
            stage_tasks = stage.get("tasks", [])
            for t_spec in stage_tasks:
                planned_tid = t_spec["task_id"]
                task_record = task_by_planned_id.get(planned_tid)

                if task_record and task_record.status in (
                    TaskStatus.SUCCEEDED.value,
                    TaskStatus.COMPLETED.value,
                ):
                    preserved_tasks.append(planned_tid)
                    continue

                if task_record and task_record.status == TaskStatus.FAILED.value:
                    any_task_failed = True
                    all_stages_complete = False
                    continue

                all_stages_complete = False

                # Evaluate dependencies
                deps = t_spec.get("dependencies", [])
                deps_satisfied = True
                dep_failed = False

                for d in deps:
                    dep_task = task_by_planned_id.get(d)
                    if not dep_task or dep_task.status not in (
                        TaskStatus.SUCCEEDED.value,
                        TaskStatus.COMPLETED.value,
                    ):
                        deps_satisfied = False
                    if dep_task and dep_task.status == TaskStatus.FAILED.value:
                        dep_failed = True

                if dep_failed:
                    # Prerequisite failed: mark blocked
                    blocked_tasks.append(planned_tid)
                    any_task_failed = True
                    if task_record:
                        task_record.status = TaskStatus.FAILED.value
                        task_record.error_message = f"Prerequisite task failed: dependencies {deps}"
                    continue

                if deps_satisfied and not any_task_failed:
                    # Eligible for resumption dispatch!
                    task_type = t_spec.get("task_type", "default").lower().strip()
                    if not task_record:
                        task_record = Task(
                            id=uuid.uuid4(),
                            workflow_id=workflow_id,
                            plan_id=plan_id,
                            planned_task_id=planned_tid,
                            name=t_spec.get("name", planned_tid),
                            description=t_spec.get("description", planned_tid),
                            task_type=task_type,
                            status=TaskStatus.QUEUED.value,
                            input_data=t_spec,
                        )
                        session.add(task_record)
                        await session.flush()
                        task_by_planned_id[planned_tid] = task_record
                    else:
                        task_record.status = TaskStatus.QUEUED.value
                        task_record.lease_token = None
                        task_record.lease_expires_at = None

                    celery_func, queue_name = self.dispatcher._resolve_task_route(task_type)
                    try:
                        job = celery_func.apply_async(
                            args=[str(workflow_id), str(plan_id), planned_tid, str(task_record.id)],
                            queue=queue_name,
                        )
                        task_record.celery_task_id = job.id
                        resumed_tasks.append(planned_tid)
                        logger.info(
                            "Resumed unblocked task '%s' in queue '%s' (celery_job: %s)",
                            planned_tid,
                            queue_name,
                            job.id,
                        )
                    except Exception as exc:
                        logger.error("Failed to re-enqueue task '%s': %s", planned_tid, exc)
                        task_record.status = TaskStatus.FAILED.value
                        task_record.error_message = f"Enqueue failure: {exc!s}"

        # 5. Update overall Workflow status
        if all_stages_complete and not any_task_failed:
            workflow.status = WorkflowStatus.COMPLETED.value
        elif any_task_failed:
            workflow.status = WorkflowStatus.FAILED.value
        else:
            workflow.status = WorkflowStatus.RUNNING.value

        await session.commit()

        return {
            "workflow_id": str(workflow_id),
            "status": workflow.status,
            "preserved_tasks_count": len(preserved_tasks),
            "preserved_tasks": preserved_tasks,
            "resumed_tasks_count": len(resumed_tasks),
            "resumed_tasks": resumed_tasks,
            "blocked_tasks_count": len(blocked_tasks),
            "blocked_tasks": blocked_tasks,
        }
