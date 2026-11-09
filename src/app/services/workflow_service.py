"""Persistence service providing CRUD operations for Workflows and Tasks."""

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.task import Task, TaskStatus
from app.db.models.workflow import Workflow, WorkflowStatus


async def create_workflow(
    session: AsyncSession,
    name: str,
    description: str | None = None,
    input_data: dict[str, Any] | None = None,
) -> Workflow:
    """Create and persist a new Workflow record."""
    workflow = Workflow(
        id=uuid.uuid4(),
        name=name,
        description=description,
        status=WorkflowStatus.PENDING.value,
        input_data=input_data or {},
    )
    session.add(workflow)
    await session.flush()
    return workflow


async def get_workflow_by_id(
    session: AsyncSession,
    workflow_id: uuid.UUID,
) -> Workflow | None:
    """Retrieve a single Workflow by its UUID primary key, including tasks."""
    query = select(Workflow).where(Workflow.id == workflow_id)
    result = await session.execute(query)
    return result.scalar_one_or_none()


async def list_workflows(
    session: AsyncSession,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[Workflow]:
    """Retrieve a paginated list of workflows ordered by creation date descending."""
    query = select(Workflow).order_by(desc(Workflow.created_at)).limit(limit).offset(offset)
    result = await session.execute(query)
    return result.scalars().all()


async def update_workflow_status(
    session: AsyncSession,
    workflow_id: uuid.UUID,
    status: WorkflowStatus | str,
    result_data: dict[str, Any] | None = None,
) -> Workflow | None:
    """Update execution lifecycle status and optional result data for a Workflow."""
    workflow = await get_workflow_by_id(session, workflow_id)
    if workflow is None:
        return None

    workflow.status = status.value if isinstance(status, WorkflowStatus) else status
    if result_data is not None:
        workflow.result_data = result_data

    await session.flush()
    return workflow


async def create_task(
    session: AsyncSession,
    workflow_id: uuid.UUID,
    name: str,
    task_type: str = "generic",
    description: str | None = None,
    input_data: dict[str, Any] | None = None,
) -> Task:
    """Create and persist a new subtask attached to a parent Workflow."""
    task = Task(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        name=name,
        task_type=task_type,
        description=description,
        status=TaskStatus.PENDING.value,
        input_data=input_data or {},
    )
    session.add(task)
    await session.flush()
    return task


async def get_tasks_by_workflow_id(
    session: AsyncSession,
    workflow_id: uuid.UUID,
) -> Sequence[Task]:
    """Retrieve all tasks associated with a given workflow ordered chronologically."""
    query = select(Task).where(Task.workflow_id == workflow_id).order_by(Task.created_at)
    result = await session.execute(query)
    return result.scalars().all()


async def update_task_status(
    session: AsyncSession,
    task_id: uuid.UUID,
    status: TaskStatus | str,
    result_data: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> Task | None:
    """Update execution status, result payload, and error trace for a Task."""
    query = select(Task).where(Task.id == task_id)
    result = await session.execute(query)
    task = result.scalar_one_or_none()
    if task is None:
        return None

    task.status = status.value if isinstance(status, TaskStatus) else status
    if result_data is not None:
        task.result_data = result_data
    if error_message is not None:
        task.error_message = error_message

    await session.flush()
    return task
