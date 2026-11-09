"""Integration tests for Fault Tolerance, Recovery, and Self-Healing REST API endpoints."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.dependencies import get_db
from app.db.models.recovery import RecoveryEventModel, WorkerNodeModel
from app.db.models.task import Task, TaskStatus
from app.db.models.workflow import WorkflowStatus
from app.services.llm.circuit_breaker import CircuitBreaker, CircuitState
from app.services.llm.service import get_llm_service
from app.services.recovery.cancellation import WorkflowCancellationService
from app.services.recovery.task_recovery_manager import TaskRecoveryManager
from app.services.recovery.workflow_recovery import WorkflowRecoveryService

# ==============================================================================
# Recovery Health & Diagnostics Endpoints
# ==============================================================================


def test_get_recovery_health_healthy(client: TestClient):
    """Verify GET /api/v1/recovery/health returns 200 with healthy cluster state."""
    now = datetime.now(UTC)
    node1 = WorkerNodeModel(
        worker_id="celery@node-1",
        hostname="host-1",
        status="ONLINE",
        last_heartbeat=now,
        registered_queues=["default", "research"],
        active_task_count=1,
    )

    mock_db = AsyncMock(spec=AsyncSession)
    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = [node1]
    mock_db.execute.return_value = mock_res

    mock_llm_svc = MagicMock()
    mock_cb = MagicMock(spec=CircuitBreaker)
    mock_cb.state = CircuitState.CLOSED
    mock_provider = MagicMock()
    mock_provider.circuit_breaker = mock_cb
    mock_llm_svc.provider = mock_provider

    client.app.dependency_overrides[get_db] = lambda: mock_db
    client.app.dependency_overrides[get_llm_service] = lambda: mock_llm_svc

    try:
        response = client.get("/api/v1/recovery/health")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "healthy"
        assert data["active_workers_count"] == 1
        assert data["unhealthy_workers_count"] == 0
        assert data["circuit_breaker_status"] == "CLOSED"
        assert "timestamp" in data
    finally:
        client.app.dependency_overrides.pop(get_llm_service, None)


def test_get_recovery_health_degraded_when_circuit_breaker_open(client: TestClient):
    """Verify GET /api/v1/recovery/health returns degraded when circuit breaker is OPEN."""
    mock_db = AsyncMock(spec=AsyncSession)
    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_res

    mock_llm_svc = MagicMock()
    mock_cb = MagicMock(spec=CircuitBreaker)
    mock_cb.state = CircuitState.OPEN
    mock_provider = MagicMock()
    mock_provider.circuit_breaker = mock_cb
    mock_llm_svc.provider = mock_provider

    client.app.dependency_overrides[get_db] = lambda: mock_db
    client.app.dependency_overrides[get_llm_service] = lambda: mock_llm_svc

    try:
        response = client.get("/api/v1/recovery/health")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "degraded"
        assert data["circuit_breaker_status"] == "OPEN"
    finally:
        client.app.dependency_overrides.pop(get_llm_service, None)


# ==============================================================================
# Worker Nodes & Heartbeat Listing
# ==============================================================================


def test_list_worker_nodes_success(client: TestClient):
    """Verify GET /api/v1/recovery/workers returns list of registered worker nodes."""
    now = datetime.now(UTC)
    node1 = WorkerNodeModel(
        worker_id="celery@worker-1",
        hostname="worker-host-1",
        status="ONLINE",
        last_heartbeat=now,
        registered_queues=["default", "research"],
        active_task_count=2,
        startup_time=now,
        worker_metadata={"concurrency": 4},
    )

    mock_db = AsyncMock(spec=AsyncSession)
    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = [node1]
    mock_db.execute.return_value = mock_res

    client.app.dependency_overrides[get_db] = lambda: mock_db

    response = client.get("/api/v1/recovery/workers")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 1
    assert data[0]["worker_id"] == "celery@worker-1"
    assert data[0]["hostname"] == "worker-host-1"
    assert data[0]["status"] == "ONLINE"
    assert data[0]["active_task_count"] == 2
    assert "research" in data[0]["registered_queues"]


# ==============================================================================
# Expired & Stuck Tasks Listing
# ==============================================================================


def test_list_expired_tasks_success(client: TestClient):
    """Verify GET /api/v1/recovery/tasks/expired returns tasks past lease expiration."""
    now = datetime.now(UTC)
    expired_task = Task(
        id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        planned_task_id="task_01",
        name="Literature Research",
        task_type="research",
        status=TaskStatus.RUNNING.value,
        worker_id="celery@node-dead",
        lease_token="lease-token-expired",
        lease_expires_at=now - timedelta(minutes=5),
        recovery_count=1,
    )

    mock_db = AsyncMock(spec=AsyncSession)
    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = [expired_task]
    mock_db.execute.return_value = mock_res

    client.app.dependency_overrides[get_db] = lambda: mock_db

    response = client.get("/api/v1/recovery/tasks/expired")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 1
    assert data[0]["task_id"] == str(expired_task.id)
    assert data[0]["planned_task_id"] == "task_01"
    assert data[0]["worker_id"] == "celery@node-dead"
    assert data[0]["recovery_count"] == 1


def test_list_stuck_tasks_success(client: TestClient):
    """Verify GET /api/v1/recovery/tasks/stuck returns stuck tasks."""
    now = datetime.now(UTC)
    stuck_task = Task(
        id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        planned_task_id="task_02",
        name="Data Analysis",
        task_type="analysis",
        status=TaskStatus.RUNNING.value,
        worker_id="celery@node-stuck",
        lease_token="lease-token-stuck",
        lease_expires_at=now - timedelta(minutes=2),
        started_at=now - timedelta(minutes=15),
        recovery_count=0,
    )

    mock_db = AsyncMock(spec=AsyncSession)
    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = [stuck_task]
    mock_db.execute.return_value = mock_res

    client.app.dependency_overrides[get_db] = lambda: mock_db

    response = client.get("/api/v1/recovery/tasks/stuck")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 1
    assert data[0]["task_id"] == str(stuck_task.id)
    assert data[0]["planned_task_id"] == "task_02"


# ==============================================================================
# Single Task Recovery Endpoint
# ==============================================================================


def test_recover_single_task_success(client: TestClient):
    """Verify POST /api/v1/recovery/tasks/{task_id} successfully triggers recovery."""
    task_id = uuid.uuid4()
    mock_outcome = {
        "task_id": str(task_id),
        "decision": "REQUEUE",
        "new_status": TaskStatus.QUEUED.value,
        "recovery_count": 1,
        "celery_task_id": "new-celery-job-123",
        "reason": "Lease expired on dead worker",
    }

    with patch.object(
        TaskRecoveryManager, "recover_task", new=AsyncMock(return_value=mock_outcome)
    ):
        response = client.post(f"/api/v1/recovery/tasks/{task_id}")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["task_id"] == str(task_id)
        assert data["decision"] == "REQUEUE"
        assert data["new_status"] == TaskStatus.QUEUED.value
        assert data["recovery_count"] == 1
        assert data["celery_task_id"] == "new-celery-job-123"


def test_recover_single_task_not_found(client: TestClient):
    """Verify POST /api/v1/recovery/tasks/{task_id} returns 404 for nonexistent task."""
    unknown_task = uuid.uuid4()

    with patch.object(TaskRecoveryManager, "recover_task", new=AsyncMock(return_value=None)):
        response = client.post(f"/api/v1/recovery/tasks/{unknown_task}")
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert f"Task with id '{unknown_task}' not found" in response.json()["detail"]


# ==============================================================================
# Workflow Resumption Endpoint
# ==============================================================================


def test_resume_workflow_success(client: TestClient):
    """Verify POST /api/v1/recovery/workflows/{workflow_id}/resume safely resumes workflow."""
    wf_id = uuid.uuid4()
    mock_resume_result = {
        "workflow_id": str(wf_id),
        "status": WorkflowStatus.RUNNING.value,
        "preserved_tasks_count": 2,
        "preserved_tasks": ["task_01", "task_02"],
        "resumed_tasks_count": 1,
        "resumed_tasks": ["task_03"],
        "blocked_tasks_count": 0,
        "blocked_tasks": [],
    }

    with patch.object(
        WorkflowRecoveryService, "resume_workflow", new=AsyncMock(return_value=mock_resume_result)
    ):
        response = client.post(f"/api/v1/recovery/workflows/{wf_id}/resume")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["workflow_id"] == str(wf_id)
        assert data["status"] == WorkflowStatus.RUNNING.value
        assert data["preserved_tasks_count"] == 2
        assert data["resumed_tasks_count"] == 1
        assert "task_03" in data["resumed_tasks"]
        assert data["blocked_tasks_count"] == 0


def test_resume_workflow_not_found(client: TestClient):
    """Verify POST /api/v1/recovery/workflows/{workflow_id}/resume returns 404 for unknown workflow."""
    unknown_wf = uuid.uuid4()

    with patch.object(
        WorkflowRecoveryService,
        "resume_workflow",
        side_effect=ValueError(f"Workflow with id '{unknown_wf}' does not exist."),
    ):
        response = client.post(f"/api/v1/recovery/workflows/{unknown_wf}/resume")
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "does not exist" in response.json()["detail"]


# ==============================================================================
# Workflow Cancellation Endpoint
# ==============================================================================


def test_cancel_workflow_execution_success(client: TestClient):
    """Verify POST /api/v1/execution/{workflow_id}/cancel successfully cancels active workflow."""
    wf_id = uuid.uuid4()
    mock_cancel_result = {
        "workflow_id": str(wf_id),
        "status": WorkflowStatus.CANCELLED.value,
        "cancelled_tasks_count": 2,
        "preserved_tasks_count": 1,
        "reason": "Workflow and active tasks safely cancelled. Completed tasks preserved.",
    }

    with patch.object(
        WorkflowCancellationService,
        "cancel_workflow",
        new=AsyncMock(return_value=mock_cancel_result),
    ):
        response = client.post(f"/api/v1/execution/{wf_id}/cancel")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["workflow_id"] == str(wf_id)
        assert data["status"] == WorkflowStatus.CANCELLED.value
        assert data["cancelled_tasks_count"] == 2
        assert data["preserved_tasks_count"] == 1
        assert "safely cancelled" in data["reason"]


def test_cancel_workflow_execution_not_found(client: TestClient):
    """Verify POST /api/v1/execution/{workflow_id}/cancel returns 404 for unknown workflow."""
    unknown_wf = uuid.uuid4()

    with patch.object(
        WorkflowCancellationService,
        "cancel_workflow",
        side_effect=ValueError(f"Workflow with id '{unknown_wf}' does not exist."),
    ):
        response = client.post(f"/api/v1/execution/{unknown_wf}/cancel")
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "does not exist" in response.json()["detail"]


# ==============================================================================
# Workflow Recovery Audit History Endpoint
# ==============================================================================


def test_get_workflow_recovery_history_success(client: TestClient):
    """Verify GET /api/v1/recovery/workflows/{workflow_id}/history returns immutable audit records."""
    wf_id = uuid.uuid4()
    task_id = uuid.uuid4()
    now = datetime.now(UTC)

    event1 = RecoveryEventModel(
        id=uuid.uuid4(),
        task_id=task_id,
        workflow_id=wf_id,
        recovery_action="REQUEUE",
        recovery_reason="Heartbeat missing and lease expired",
        previous_status=TaskStatus.RUNNING.value,
        new_status=TaskStatus.QUEUED.value,
        worker_id="celery@worker-crashed",
        lease_token="expired-token-123",
        retry_count=1,
        created_at=now,
    )

    mock_db = AsyncMock(spec=AsyncSession)
    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = [event1]
    mock_db.execute.return_value = mock_res

    client.app.dependency_overrides[get_db] = lambda: mock_db

    response = client.get(f"/api/v1/recovery/workflows/{wf_id}/history")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 1
    assert data[0]["workflow_id"] == str(wf_id)
    assert data[0]["task_id"] == str(task_id)
    assert data[0]["recovery_action"] == "REQUEUE"
    assert data[0]["recovery_reason"] == "Heartbeat missing and lease expired"
    assert data[0]["worker_id"] == "celery@worker-crashed"
