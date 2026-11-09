"""Celery worker tasks executing specialized AI agents with atomic claims, retries, and persistence."""

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.agents.contracts import AgentExecutionStatus, AgentInput
from app.core.config import get_settings
from app.db.models.execution_plan import ExecutionPlanModel
from app.db.models.task import Task, TaskStatus
from app.db.models.workflow import Workflow, WorkflowStatus
from app.services.agent_execution_service import (
    AgentExecutionService,
    get_agent_execution_service,
)
from app.workers.celery_app import celery_app

logger = logging.getLogger("app.workers.tasks")


@asynccontextmanager
async def get_worker_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide an isolated AsyncSession with NullPool for Celery worker processes.

    Prevents connection pool leakage across forked Celery worker processes and separate asyncio event loops.
    """
    settings = get_settings()
    url = settings.async_database_url
    connect_args: dict[str, object] = {}
    if "asyncmy" in url:
        connect_args["charset"] = "utf8mb4"

    engine = create_async_engine(
        url,
        poolclass=NullPool,
        echo=False,
        connect_args=connect_args,
    )
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        autoflush=False,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await engine.dispose()


async def _async_execute_agent_task(
    workflow_id_str: str,
    plan_id_str: str,
    planned_task_id: str,
    worker_name: str,
    task_record_id_str: str | None = None,
    execution_service: AgentExecutionService | None = None,
) -> dict[str, Any]:
    """Execute specialized agent task asynchronously within isolated database session."""
    workflow_uuid = uuid.UUID(workflow_id_str)
    plan_uuid = uuid.UUID(plan_id_str)
    task_record_uuid = uuid.UUID(task_record_id_str) if task_record_id_str else None

    exec_service = execution_service or get_agent_execution_service()

    async with get_worker_session() as session:
        # 1. Retrieve or locate Task record
        if task_record_uuid:
            query = select(Task).where(Task.id == task_record_uuid)
        else:
            query = select(Task).where(
                Task.workflow_id == workflow_uuid,
                Task.planned_task_id == planned_task_id,
            )
        result = await session.execute(query)
        task_model = result.scalar_one_or_none()

        if task_model is None:
            raise ValueError(f"Task record for planned task '{planned_task_id}' not found.")

        # 2. Idempotency Check: Do not execute if already completed or succeeded
        if task_model.status in (TaskStatus.SUCCEEDED.value, TaskStatus.COMPLETED.value):
            logger.info(
                "Task '%s' already %s. Skipping duplicate execution (idempotent guard).",
                planned_task_id,
                task_model.status,
            )
            return {
                "task_id": str(task_model.id),
                "planned_task_id": planned_task_id,
                "status": task_model.status,
                "message": "Already completed",
            }

        # 3. Atomically acquire task lease & record worker heartbeat
        from app.services.recovery.failure_detector import WorkerFailureDetector
        from app.services.recovery.lease_manager import TaskLeaseManager

        lease_manager = TaskLeaseManager()
        detector = WorkerFailureDetector()

        try:
            await detector.record_heartbeat(
                worker_id=worker_name,
                session=session,
                active_tasks=[{"task_id": str(task_model.id), "planned_task_id": planned_task_id}],
            )
        except Exception as hb_exc:
            logger.debug("Failed to update heartbeat during task claim: %s", hb_exc)

        lease_token = await lease_manager.acquire_lease(
            task_id=task_model.id,
            worker_id=worker_name,
            session=session,
        )
        if not lease_token:
            logger.warning(
                "Could not acquire lease for task '%s'. Owned by another active worker or in non-claimable state.",
                planned_task_id,
            )
            return {
                "task_id": str(task_model.id),
                "planned_task_id": planned_task_id,
                "status": task_model.status,
                "message": "Lease unavailable / already owned",
            }

        # Update Workflow status to RUNNING if currently PENDING or QUEUED
        wf_query = select(Workflow).where(Workflow.id == workflow_uuid)
        wf_res = await session.execute(wf_query)
        wf_model = wf_res.scalar_one_or_none()
        if wf_model and wf_model.status in (WorkflowStatus.PENDING.value, "QUEUED"):
            wf_model.status = WorkflowStatus.RUNNING.value
            await session.flush()

        # 4. Retrieve execution plan and task specifications
        plan_query = select(ExecutionPlanModel).where(ExecutionPlanModel.id == plan_uuid)
        plan_res = await session.execute(plan_query)
        plan_model = plan_res.scalar_one_or_none()
        if not plan_model:
            raise ValueError(f"Execution plan with id '{plan_uuid}' not found.")

        # Find matching task in plan
        tasks_data = plan_model.tasks_data or []
        target_spec = next((t for t in tasks_data if t.get("task_id") == planned_task_id), None)
        if not target_spec:
            raise ValueError(f"Planned task '{planned_task_id}' not found in plan data.")

        assigned_agent = target_spec.get("recommended_agent_id", "researcher")
        task_description = target_spec.get("description", target_spec.get("name", planned_task_id))

        # 5. Execute agent through AgentExecutionService
        agent_input = AgentInput(
            task_id=str(task_model.id),
            workflow_id=str(workflow_uuid),
            task_description=task_description,
            context={
                "plan_objective": plan_model.objective,
                "planned_task_id": planned_task_id,
                "required_capabilities": target_spec.get("required_capabilities", []),
                "task_input_data": task_model.input_data,
            },
            expected_output=target_spec.get("expected_output", "Verifiable deliverable"),
        )

        agent_output = await exec_service.execute_agent(
            agent_id=assigned_agent,
            input_data=agent_input,
            session=session,
        )

        # 6. Update Task Status & Release Lease with Token Verification
        if agent_output.status == AgentExecutionStatus.SUCCESS:
            result_payload = (
                agent_output.result
                if isinstance(agent_output.result, dict)
                else {"content": agent_output.result}
            )
            released = await lease_manager.release_lease(
                task_id=task_model.id,
                lease_token=lease_token,
                target_status=TaskStatus.SUCCEEDED,
                session=session,
                result_data=result_payload,
            )
            if not released:
                raise RuntimeError(
                    f"Lost lease ownership during execution of task '{planned_task_id}'. Cannot commit result."
                )
            task_model.status = TaskStatus.SUCCEEDED.value
            task_model.result_data = result_payload
            task_model.completed_at = datetime.now(UTC)
            logger.info(
                "Worker task '%s' SUCCEEDED via agent '%s'", planned_task_id, assigned_agent
            )
        else:
            task_model.last_error_category = "AGENT_FAILURE"
            await lease_manager.release_lease(
                task_id=task_model.id,
                lease_token=lease_token,
                target_status=TaskStatus.FAILED,
                session=session,
                error_message=agent_output.error or "Agent execution failed.",
            )
            task_model.status = TaskStatus.FAILED.value
            logger.warning(
                "Worker task '%s' FAILED via agent '%s': %s",
                planned_task_id,
                assigned_agent,
                agent_output.error,
            )

        await session.flush()

        # 7. Check if subsequent stage can advance
        from app.services.task_dispatcher import DistributedTaskDispatcher

        dispatcher = DistributedTaskDispatcher()
        await dispatcher.advance_workflow_stages(workflow_id=workflow_uuid, session=session)

        return {
            "task_id": str(task_model.id),
            "planned_task_id": planned_task_id,
            "status": task_model.status,
            "agent_id": assigned_agent,
            "completed_at": task_model.completed_at.isoformat()
            if task_model.completed_at
            else None,
        }


def _run_async_in_worker(coro: Any) -> Any:
    """Safely execute an asynchronous coroutine within Celery worker execution context.

    If an asyncio event loop is already active in the current thread (e.g., during tests
    or eager mode), dispatches execution to an isolated background thread to prevent
    'RuntimeError: asyncio.run() cannot be called from a running event loop'.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=5,
    name="app.workers.tasks.execution_tasks.execute_agent_task",
)
def execute_agent_task(
    self: Any,
    workflow_id: str,
    plan_id: str,
    planned_task_id: str,
    task_record_id: str | None = None,
) -> dict[str, Any]:
    """Celery task executing specialized AI agent in worker process with exponential backoff retries."""
    worker_name = getattr(self.request, "hostname", "unknown_worker")
    logger.info(
        "Celery worker '%s' processing task '%s' (workflow: %s, attempt: %d)",
        worker_name,
        planned_task_id,
        workflow_id,
        self.request.retries + 1,
    )

    try:
        return _run_async_in_worker(
            _async_execute_agent_task(
                workflow_id_str=workflow_id,
                plan_id_str=plan_id,
                planned_task_id=planned_task_id,
                worker_name=worker_name,
                task_record_id_str=task_record_id,
            )
        )
    except Exception as exc:
        from app.services.recovery.retry_policy import RetryPolicy

        policy = RetryPolicy()
        decision = policy.evaluate(
            exc=exc,
            current_retry_count=self.request.retries,
            max_retries=self.max_retries,
        )

        logger.warning(
            "Worker task '%s' encountered [%s] error: %s (attempt %d/%d, should_retry: %s)",
            planned_task_id,
            decision.error_category.value,
            exc,
            self.request.retries + 1,
            self.max_retries,
            decision.should_retry,
        )
        if decision.should_retry:
            raise self.retry(exc=exc, countdown=int(decision.delay_seconds)) from exc
        raise


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=5,
    name="app.workers.tasks.execution_tasks.execute_research_task",
)
def execute_research_task(
    _self: Any,
    workflow_id: str,
    plan_id: str,
    planned_task_id: str,
    task_record_id: str | None = None,
) -> dict[str, Any]:
    """Specialized worker task routed to research queue."""
    return execute_agent_task(
        workflow_id=workflow_id,
        plan_id=plan_id,
        planned_task_id=planned_task_id,
        task_record_id=task_record_id,
    )


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=5,
    name="app.workers.tasks.execution_tasks.execute_analysis_task",
)
def execute_analysis_task(
    _self: Any,
    workflow_id: str,
    plan_id: str,
    planned_task_id: str,
    task_record_id: str | None = None,
) -> dict[str, Any]:
    """Specialized worker task routed to analysis queue."""
    return execute_agent_task(
        workflow_id=workflow_id,
        plan_id=plan_id,
        planned_task_id=planned_task_id,
        task_record_id=task_record_id,
    )


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=5,
    name="app.workers.tasks.execution_tasks.execute_coding_task",
)
def execute_coding_task(
    _self: Any,
    workflow_id: str,
    plan_id: str,
    planned_task_id: str,
    task_record_id: str | None = None,
) -> dict[str, Any]:
    """Specialized worker task routed to coding queue."""
    return execute_agent_task(
        workflow_id=workflow_id,
        plan_id=plan_id,
        planned_task_id=planned_task_id,
        task_record_id=task_record_id,
    )
