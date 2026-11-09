"""Unit tests for database models, relationships, and persistence services."""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.task import Task, TaskStatus
from app.db.models.workflow import Workflow, WorkflowStatus
from app.db.session import (
    check_database_health,
    close_database_connections,
    get_engine,
    get_session_factory,
)
from app.services.workflow_service import (
    create_task,
    create_workflow,
    get_tasks_by_workflow_id,
    get_workflow_by_id,
    list_workflows,
    update_task_status,
    update_workflow_status,
)


def test_workflow_model_instantiation():
    """Verify that Workflow model initializes with expected attributes and defaults."""
    workflow_id = uuid.uuid4()
    workflow = Workflow(
        id=workflow_id,
        name="Data Extraction Workflow",
        description="Extract and structure financial tables",
        status=WorkflowStatus.PENDING.value,
        input_data={"source_url": "https://example.com/data"},
    )

    assert workflow.id == workflow_id
    assert workflow.name == "Data Extraction Workflow"
    assert workflow.description == "Extract and structure financial tables"
    assert workflow.status == WorkflowStatus.PENDING.value
    assert workflow.input_data == {"source_url": "https://example.com/data"}
    assert workflow.result_data is None


def test_task_model_instantiation():
    """Verify that Task model initializes with expected attributes and defaults."""
    task_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    task = Task(
        id=task_id,
        workflow_id=workflow_id,
        name="Fetch Raw HTML",
        task_type="researcher",
        status=TaskStatus.PENDING.value,
        description="Query external website",
        input_data={"timeout": 30},
    )

    assert task.id == task_id
    assert task.workflow_id == workflow_id
    assert task.name == "Fetch Raw HTML"
    assert task.task_type == "researcher"
    assert task.status == TaskStatus.PENDING.value
    assert task.input_data == {"timeout": 30}
    assert task.result_data is None
    assert task.error_message is None


def test_workflow_task_relationship_link():
    """Verify bidirectional in-memory relationship between Workflow and Task."""
    workflow = Workflow(
        name="Parent Workflow",
        status=WorkflowStatus.PENDING.value,
        input_data={},
    )
    task1 = Task(
        name="Child Task 1",
        task_type="researcher",
        status=TaskStatus.PENDING.value,
        input_data={},
    )
    task2 = Task(
        name="Child Task 2",
        task_type="coder",
        status=TaskStatus.PENDING.value,
        input_data={},
    )

    workflow.tasks.append(task1)
    workflow.tasks.append(task2)

    assert len(workflow.tasks) == 2
    assert task1.workflow == workflow
    assert task2.workflow == workflow


def test_workflow_crud_operations():
    """Verify workflow persistence, retrieval, status updates, and listing with AsyncSession."""

    async def _run():
        mock_session = AsyncMock(spec=AsyncSession)

        # 1. Create Workflow
        wf = await create_workflow(
            session=mock_session,
            name="Research Plan",
            description="Perform deep market research",
            input_data={"topic": "Agentic AI"},
        )
        assert wf.id is not None
        assert wf.name == "Research Plan"
        assert wf.status == WorkflowStatus.PENDING.value
        mock_session.add.assert_called_once_with(wf)
        mock_session.flush.assert_awaited()

        # 2. Retrieve Workflow by ID
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = wf
        mock_session.execute.return_value = mock_result

        retrieved = await get_workflow_by_id(mock_session, wf.id)
        assert retrieved is not None
        assert retrieved.id == wf.id

        # 3. Update Workflow Status
        updated = await update_workflow_status(
            session=mock_session,
            workflow_id=wf.id,
            status=WorkflowStatus.RUNNING,
        )
        assert updated is not None
        assert updated.status == WorkflowStatus.RUNNING.value

        # 4. Update Workflow Completion with Result
        completed = await update_workflow_status(
            session=mock_session,
            workflow_id=wf.id,
            status=WorkflowStatus.COMPLETED,
            result_data={"summary": "Market analysis complete", "confidence": 0.95},
        )
        assert completed is not None
        assert completed.status == WorkflowStatus.COMPLETED.value
        assert completed.result_data["confidence"] == 0.95

        # 5. List Workflows
        mock_list_result = MagicMock()
        mock_list_result.scalars.return_value.all.return_value = [wf]
        mock_session.execute.return_value = mock_list_result

        wf_list = await list_workflows(mock_session, limit=10)
        assert len(wf_list) == 1
        assert wf_list[0].id == wf.id

    asyncio.run(_run())


def test_task_crud_operations():
    """Verify task creation, relationship linkage, retrieval, and status updates with AsyncSession."""

    async def _run():
        mock_session = AsyncMock(spec=AsyncSession)
        workflow_id = uuid.uuid4()

        # 1. Create subtask
        task = await create_task(
            session=mock_session,
            workflow_id=workflow_id,
            name="Execute Python AST Check",
            task_type="coder",
            description="Verify syntax safety",
            input_data={"code": "x = 42"},
        )
        assert task.id is not None
        assert task.workflow_id == workflow_id
        assert task.task_type == "coder"
        assert task.status == TaskStatus.PENDING.value
        mock_session.add.assert_called_with(task)

        # 2. Retrieve tasks for workflow
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [task]
        mock_session.execute.return_value = mock_result

        tasks = await get_tasks_by_workflow_id(mock_session, workflow_id)
        assert len(tasks) == 1
        assert tasks[0].id == task.id

        # 3. Update task to RUNNING
        mock_single_result = MagicMock()
        mock_single_result.scalar_one_or_none.return_value = task
        mock_session.execute.return_value = mock_single_result

        running_task = await update_task_status(
            session=mock_session,
            task_id=task.id,
            status=TaskStatus.RUNNING,
        )
        assert running_task is not None
        assert running_task.status == TaskStatus.RUNNING.value

        # 4. Update task to COMPLETED with result payload
        completed_task = await update_task_status(
            session=mock_session,
            task_id=task.id,
            status=TaskStatus.COMPLETED,
            result_data={"ast_valid": True, "nodes_checked": 5},
        )
        assert completed_task is not None
        assert completed_task.status == TaskStatus.COMPLETED.value
        assert completed_task.result_data["ast_valid"] is True

    asyncio.run(_run())


def test_engine_initialization_and_disposal():
    """Verify AsyncEngine creation, session factory initialization, and shutdown disposal."""

    async def _run():
        engine = get_engine()
        assert engine is not None

        factory = get_session_factory()
        assert factory is not None

        # Test closing connections
        await close_database_connections()

        # Re-initialization should create a fresh engine
        new_engine = get_engine()
        assert new_engine is not None
        await close_database_connections()

    asyncio.run(_run())


def test_check_database_health_unreachable():
    """Verify check_database_health returns False when connection is unreachable."""

    async def _run():
        with patch(
            "app.db.session.get_session",
            side_effect=ConnectionRefusedError("MySQL port unreachable"),
        ):
            result = await check_database_health(timeout_seconds=0.1)
            assert result is False

    asyncio.run(_run())
