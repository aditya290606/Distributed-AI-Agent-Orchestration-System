"""Centralized DistributedTaskDispatcher coordinating dependency-aware dispatch to Celery queues."""

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.execution_plan import ExecutionPlanModel
from app.db.models.task import Task, TaskStatus
from app.db.models.workflow import Workflow, WorkflowStatus
from app.workers.tasks.execution_tasks import (
    execute_agent_task,
    execute_analysis_task,
    execute_coding_task,
    execute_research_task,
)

logger = logging.getLogger("app.services.task_dispatcher")


class DistributedTaskDispatcher:
    """Dispatches planned tasks to Celery worker queues respecting DAG dependencies and stages."""

    async def dispatch_plan(
        self,
        plan_id: uuid.UUID,
        session: AsyncSession,
    ) -> dict[str, Any]:
        """Validate execution plan and dispatch its first eligible execution stage to Celery.

        Args:
            plan_id: UUID of the execution plan to execute.
            session: Active database session.

        Returns:
            Dictionary detailing workflow ID, plan ID, and list of dispatched tasks.
        """
        # 1. Load Execution Plan
        query = select(ExecutionPlanModel).where(ExecutionPlanModel.id == plan_id)
        res = await session.execute(query)
        plan_model = res.scalar_one_or_none()
        if not plan_model:
            raise ValueError(f"Execution plan with id '{plan_id}' does not exist.")

        # 2. Ensure an associated Workflow exists
        workflow_id = plan_model.workflow_id
        if workflow_id:
            wf_query = select(Workflow).where(Workflow.id == workflow_id)
            wf_res = await session.execute(wf_query)
            workflow = wf_res.scalar_one_or_none()
        else:
            workflow = None

        if not workflow:
            workflow_id = uuid.uuid4()
            workflow = Workflow(
                id=workflow_id,
                name=f"Workflow for Plan {str(plan_id)[:8]}",
                description=plan_model.objective,
                status=WorkflowStatus.RUNNING.value,
                input_data={"plan_id": str(plan_id)},
            )
            session.add(workflow)
            plan_model.workflow_id = workflow_id
            await session.flush()
        else:
            workflow.status = WorkflowStatus.RUNNING.value
            await session.flush()

        stages_data = plan_model.stages_data or []
        if not stages_data:
            raise ValueError(f"Execution plan '{plan_id}' contains no execution stages.")

        # 3. Dispatch tasks in Stage 1
        stage_1 = stages_data[0]
        stage_tasks = stage_1.get("tasks", [])
        dispatched_list: list[dict[str, Any]] = []

        for t_spec in stage_tasks:
            planned_tid = t_spec["task_id"]
            task_type = t_spec.get("task_type", "default").lower().strip()

            # Find or create Task record
            task_query = select(Task).where(
                Task.workflow_id == workflow_id,
                Task.planned_task_id == planned_tid,
            )
            task_res = await session.execute(task_query)
            task_record = task_res.scalar_one_or_none()

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
            elif task_record.status in (TaskStatus.SUCCEEDED.value, TaskStatus.COMPLETED.value):
                logger.info("Task '%s' already completed. Skipping dispatch.", planned_tid)
                continue
            else:
                task_record.status = TaskStatus.QUEUED.value
                await session.flush()

            # Dispatch to appropriate queue
            celery_func, queue_name = self._resolve_task_route(task_type)
            try:
                celery_job = celery_func.apply_async(
                    args=[str(workflow_id), str(plan_id), planned_tid, str(task_record.id)],
                    queue=queue_name,
                )
                task_record.celery_task_id = celery_job.id
                await session.flush()

                dispatched_list.append(
                    {
                        "task_id": str(task_record.id),
                        "planned_task_id": planned_tid,
                        "task_type": task_type,
                        "queue": queue_name,
                        "celery_task_id": celery_job.id,
                    }
                )
                logger.info(
                    "Dispatched task '%s' to queue '%s' (celery_id: %s)",
                    planned_tid,
                    queue_name,
                    celery_job.id,
                )
            except Exception as e:
                logger.error("Failed to enqueue task '%s' to broker: %s", planned_tid, e)
                task_record.status = TaskStatus.FAILED.value
                task_record.error_message = f"Broker enqueue failure: {e!s}"
                await session.flush()

        return {
            "workflow_id": str(workflow_id),
            "plan_id": str(plan_id),
            "dispatched_stage": stage_1.get("stage_number", 1),
            "dispatched_tasks_count": len(dispatched_list),
            "dispatched_tasks": dispatched_list,
            "status": "DISPATCHED" if dispatched_list else "NO_TASKS_DISPATCHED",
        }

    async def advance_workflow_stages(
        self,
        workflow_id: uuid.UUID,
        session: AsyncSession,
    ) -> list[str]:
        """Inspect task completion across all stages and dispatch newly unblocked stages.

        Args:
            workflow_id: UUID of the workflow to inspect.
            session: Active database session.

        Returns:
            List of newly dispatched task_ids.
        """
        # Fetch all tasks for this workflow
        tasks_query = select(Task).where(Task.workflow_id == workflow_id)
        tasks_res = await session.execute(tasks_query)
        all_tasks = list(tasks_res.scalars().all())

        task_status_by_planned_id: dict[str, str] = {
            t.planned_task_id: t.status for t in all_tasks if t.planned_task_id
        }

        # Check plan
        plan_id = all_tasks[0].plan_id if all_tasks and all_tasks[0].plan_id else None
        if not plan_id:
            return []

        plan_query = select(ExecutionPlanModel).where(ExecutionPlanModel.id == plan_id)
        plan_res = await session.execute(plan_query)
        plan_model = plan_res.scalar_one_or_none()
        if not plan_model or not plan_model.stages_data:
            return []

        stages_data = plan_model.stages_data
        newly_dispatched: list[str] = []

        all_stages_completed = True
        any_task_failed = any(t.status == TaskStatus.FAILED.value for t in all_tasks)

        for stage in stages_data:
            stage_tasks = stage.get("tasks", [])
            for t_spec in stage_tasks:
                planned_tid = t_spec["task_id"]
                current_status = task_status_by_planned_id.get(
                    planned_tid, TaskStatus.PENDING.value
                )

                if current_status not in (TaskStatus.SUCCEEDED.value, TaskStatus.COMPLETED.value):
                    all_stages_completed = False

                # Check if all dependencies are satisfied
                deps = t_spec.get("dependencies", [])
                deps_satisfied = all(
                    task_status_by_planned_id.get(d)
                    in (TaskStatus.SUCCEEDED.value, TaskStatus.COMPLETED.value)
                    for d in deps
                )

                if (
                    current_status == TaskStatus.PENDING.value
                    and deps_satisfied
                    and not any_task_failed
                ):
                    # Eligible for dispatch!
                    task_type = t_spec.get("task_type", "default").lower().strip()
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

                    celery_func, queue_name = self._resolve_task_route(task_type)
                    try:
                        celery_job = celery_func.apply_async(
                            args=[str(workflow_id), str(plan_id), planned_tid, str(task_record.id)],
                            queue=queue_name,
                        )
                        task_record.celery_task_id = celery_job.id
                        newly_dispatched.append(planned_tid)
                        task_status_by_planned_id[planned_tid] = TaskStatus.QUEUED.value
                        await session.flush()
                        logger.info(
                            "Advanced stage: dispatched unblocked task '%s' to '%s'",
                            planned_tid,
                            queue_name,
                        )
                    except Exception as e:
                        logger.error("Failed to enqueue unblocked task '%s': %s", planned_tid, e)

        # Update overall Workflow status
        wf_query = select(Workflow).where(Workflow.id == workflow_id)
        wf_res = await session.execute(wf_query)
        wf = wf_res.scalar_one_or_none()
        if wf:
            if all_stages_completed and not any_task_failed:
                wf.status = WorkflowStatus.COMPLETED.value
                logger.info("Workflow '%s' marked COMPLETED: all stages succeeded", workflow_id)
            elif any_task_failed:
                wf.status = WorkflowStatus.FAILED.value
                logger.warning("Workflow '%s' marked FAILED due to subtask failure", workflow_id)
            await session.flush()

        return newly_dispatched

    def _resolve_task_route(self, task_type: str) -> tuple[Any, str]:
        """Map task classification to Celery function and designated queue."""
        tt = task_type.lower().strip()
        if "research" in tt:
            return execute_research_task, "research"
        if "analysis" in tt or "analyst" in tt:
            return execute_analysis_task, "analysis"
        if "code" in tt or "coding" in tt:
            return execute_coding_task, "coding"
        return execute_agent_task, "default"
