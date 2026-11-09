"""Integration tests for Distributed Execution and Celery Worker API endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.dependencies import get_db
from app.db.models.task import Task, TaskStatus
from app.db.models.workflow import Workflow, WorkflowStatus

# ==============================================================================
# Execution Plan Submission Endpoints
# ==============================================================================


def test_submit_execution_plan_success(client: TestClient):
    """Verify POST /api/v1/execution/submit/{plan_id} returns 202 and dispatch summary."""
    plan_uuid = uuid.uuid4()
    workflow_uuid = uuid.uuid4()

    mock_dispatch_result = {
        "workflow_id": str(workflow_uuid),
        "plan_id": str(plan_uuid),
        "dispatched_stage": 1,
        "dispatched_tasks_count": 2,
        "dispatched_tasks": [
            {
                "task_id": str(uuid.uuid4()),
                "planned_task_id": "task_01",
                "task_type": "research",
                "queue": "research",
                "celery_task_id": "celery-id-1",
            },
            {
                "task_id": str(uuid.uuid4()),
                "planned_task_id": "task_02",
                "task_type": "analysis",
                "queue": "analysis",
                "celery_task_id": "celery-id-2",
            },
        ],
        "status": "DISPATCHED",
    }

    with patch(
        "app.services.task_dispatcher.DistributedTaskDispatcher.dispatch_plan",
        new=AsyncMock(return_value=mock_dispatch_result),
    ):
        response = client.post(f"/api/v1/execution/submit/{plan_uuid}")
        assert response.status_code == status.HTTP_202_ACCEPTED
        data = response.json()
        assert data["workflow_id"] == str(workflow_uuid)
        assert data["plan_id"] == str(plan_uuid)
        assert data["dispatched_stage"] == 1
        assert data["dispatched_tasks_count"] == 2
        assert len(data["dispatched_tasks"]) == 2
        assert data["status"] == "DISPATCHED"


def test_submit_execution_plan_not_found(client: TestClient):
    """Verify POST /api/v1/execution/submit/{plan_id} returns 404 for unknown plan."""
    unknown_plan = uuid.uuid4()

    with patch(
        "app.services.task_dispatcher.DistributedTaskDispatcher.dispatch_plan",
        side_effect=ValueError(f"Execution plan with id '{unknown_plan}' does not exist."),
    ):
        response = client.post(f"/api/v1/execution/submit/{unknown_plan}")
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "does not exist" in response.json()["detail"]


def test_submit_execution_plan_no_stages(client: TestClient):
    """Verify POST /api/v1/execution/submit/{plan_id} returns 400 for empty plan."""
    invalid_plan = uuid.uuid4()

    with patch(
        "app.services.task_dispatcher.DistributedTaskDispatcher.dispatch_plan",
        side_effect=ValueError(f"Execution plan '{invalid_plan}' contains no execution stages."),
    ):
        response = client.post(f"/api/v1/execution/submit/{invalid_plan}")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "no execution stages" in response.json()["detail"]


# ==============================================================================
# Execution Summary and Task List Endpoints
# ==============================================================================


def test_get_execution_summary_success(client: TestClient):
    """Verify GET /api/v1/execution/{workflow_id} returns 200 with aggregate metrics."""
    wf_id = uuid.uuid4()
    now = datetime.now(UTC)

    mock_wf = MagicMock(spec=Workflow)
    mock_wf.id = wf_id
    mock_wf.status = WorkflowStatus.RUNNING.value
    mock_wf.created_at = now
    mock_wf.updated_at = now

    mock_session = AsyncMock(spec=AsyncSession)

    # 1st query: Workflow scalar; 2nd query: Grouped task status counts
    def mock_execute_side_effect(query):
        mock_res = MagicMock()
        q_str = str(query)
        if "workflows" in q_str:
            mock_res.scalar_one_or_none.return_value = mock_wf
        else:
            mock_res.all.return_value = [("SUCCEEDED", 2), ("QUEUED", 1)]
        return mock_res

    mock_session.execute.side_effect = mock_execute_side_effect

    client.app.dependency_overrides[get_db] = lambda: mock_session
    try:
        response = client.get(f"/api/v1/execution/{wf_id}")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["workflow_id"] == str(wf_id)
        assert data["status"] == "RUNNING"
        assert data["total_tasks"] == 3
        assert data["tasks_by_status"] == {"SUCCEEDED": 2, "QUEUED": 1}
        assert data["elapsed_seconds"] is not None
    finally:
        client.app.dependency_overrides.pop(get_db, None)


def test_get_execution_summary_not_found(client: TestClient):
    """Verify GET /api/v1/execution/{workflow_id} returns 404 for unknown workflow."""
    wf_id = uuid.uuid4()
    mock_session = AsyncMock(spec=AsyncSession)
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)

    client.app.dependency_overrides[get_db] = lambda: mock_session
    try:
        response = client.get(f"/api/v1/execution/{wf_id}")
        assert response.status_code == status.HTTP_404_NOT_FOUND
    finally:
        client.app.dependency_overrides.pop(get_db, None)


def test_list_workflow_tasks_success(client: TestClient):
    """Verify GET /api/v1/execution/{workflow_id}/tasks returns list of tasks."""
    wf_id = uuid.uuid4()
    task_id = uuid.uuid4()
    now = datetime.now(UTC)

    mock_wf = MagicMock(spec=Workflow)
    mock_wf.id = wf_id

    mock_task = MagicMock(spec=Task)
    mock_task.id = task_id
    mock_task.workflow_id = wf_id
    mock_task.plan_id = uuid.uuid4()
    mock_task.planned_task_id = "task_01"
    mock_task.name = "Analyze data"
    mock_task.task_type = "analysis"
    mock_task.status = TaskStatus.SUCCEEDED.value
    mock_task.celery_task_id = "celery-task-99"
    mock_task.worker_id = "worker@node-1"
    mock_task.retry_count = 0
    mock_task.started_at = now
    mock_task.completed_at = now
    mock_task.created_at = now
    mock_task.updated_at = now

    mock_session = AsyncMock(spec=AsyncSession)

    def mock_execute_side_effect(query):
        mock_res = MagicMock()
        q_str = str(query)
        if "workflows" in q_str:
            mock_res.scalar_one_or_none.return_value = mock_wf
        else:
            mock_res.scalars.return_value.all.return_value = [mock_task]
        return mock_res

    mock_session.execute.side_effect = mock_execute_side_effect

    client.app.dependency_overrides[get_db] = lambda: mock_session
    try:
        response = client.get(f"/api/v1/execution/{wf_id}/tasks")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == str(task_id)
        assert data[0]["planned_task_id"] == "task_01"
        assert data[0]["status"] == "SUCCEEDED"
        assert data[0]["worker_id"] == "worker@node-1"
    finally:
        client.app.dependency_overrides.pop(get_db, None)


def test_get_task_detail_success(client: TestClient):
    """Verify GET /api/v1/execution/tasks/{task_id} returns full diagnostics."""
    wf_id = uuid.uuid4()
    task_id = uuid.uuid4()
    now = datetime.now(UTC)

    mock_task = MagicMock(spec=Task)
    mock_task.id = task_id
    mock_task.workflow_id = wf_id
    mock_task.plan_id = uuid.uuid4()
    mock_task.planned_task_id = "task_01"
    mock_task.name = "Analyze architecture"
    mock_task.description = "Deep inspect components"
    mock_task.task_type = "analysis"
    mock_task.status = TaskStatus.SUCCEEDED.value
    mock_task.celery_task_id = "celery-task-99"
    mock_task.worker_id = "worker@node-1"
    mock_task.retry_count = 0
    mock_task.started_at = now
    mock_task.completed_at = now
    mock_task.input_data = {"key": "value"}
    mock_task.result_data = {"summary": "clean architecture"}
    mock_task.error_message = None
    mock_task.created_at = now
    mock_task.updated_at = now

    mock_session = AsyncMock(spec=AsyncSession)
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_task
    mock_session.execute = AsyncMock(return_value=mock_result)

    client.app.dependency_overrides[get_db] = lambda: mock_session
    try:
        response = client.get(f"/api/v1/execution/tasks/{task_id}")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["id"] == str(task_id)
        assert data["description"] == "Deep inspect components"
        assert data["result_data"] == {"summary": "clean architecture"}
        assert data["error_message"] is None
    finally:
        client.app.dependency_overrides.pop(get_db, None)


def test_get_task_detail_not_found(client: TestClient):
    """Verify GET /api/v1/execution/tasks/{task_id} returns 404 for unknown task."""
    task_id = uuid.uuid4()
    mock_session = AsyncMock(spec=AsyncSession)
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)

    client.app.dependency_overrides[get_db] = lambda: mock_session
    try:
        response = client.get(f"/api/v1/execution/tasks/{task_id}")
        assert response.status_code == status.HTTP_404_NOT_FOUND
    finally:
        client.app.dependency_overrides.pop(get_db, None)


# ==============================================================================
# Worker Health and Registry Endpoints
# ==============================================================================


def test_get_workers_health_api(client: TestClient):
    """Verify GET /api/v1/workers/health returns 200 with broker and worker metrics."""
    mock_health = {
        "status": "healthy",
        "timestamp": datetime.now(UTC).isoformat(),
        "broker": {
            "status": "healthy",
            "broker_url": "redis://localhost:6379/0",
            "redis_version": "7.2.4",
            "connected_clients": 4,
            "error": None,
        },
        "workers_online": 1,
        "workers": [
            {
                "worker_name": "celery@node-1",
                "status": "online",
                "active_tasks_count": 0,
                "active_tasks": [],
                "queues": ["default", "research"],
                "concurrency": 2,
                "pool_type": "solo",
                "last_seen": datetime.now(UTC).isoformat(),
            }
        ],
    }

    with patch("app.api.routes.workers.get_worker_health_summary", return_value=mock_health):
        response = client.get("/api/v1/workers/health")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "healthy"
        assert data["broker"]["status"] == "healthy"
        assert data["workers_online"] == 1
        assert len(data["workers"]) == 1


def test_get_active_workers_api(client: TestClient):
    """Verify GET /api/v1/workers returns 200 with list of worker details."""
    mock_workers = [
        {
            "worker_name": "celery@node-1",
            "status": "online",
            "active_tasks_count": 1,
            "active_tasks": [{"task_id": "job-1", "name": "research_task"}],
            "queues": ["research", "default"],
            "concurrency": 1,
            "pool_type": "solo",
            "last_seen": datetime.now(UTC).isoformat(),
        }
    ]

    with patch("app.api.routes.workers.list_active_workers", return_value=mock_workers):
        response = client.get("/api/v1/workers")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 1
        assert data[0]["worker_name"] == "celery@node-1"
        assert data[0]["active_tasks_count"] == 1
        assert data[0]["queues"] == ["research", "default"]
