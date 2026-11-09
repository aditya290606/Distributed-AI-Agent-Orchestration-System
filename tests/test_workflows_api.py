"""Integration tests for Workflow Orchestration API endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from fastapi import status
from fastapi.testclient import TestClient

from app.db.models.task import Task
from app.db.models.workflow import Workflow, WorkflowStatus


def test_execute_workflow_api_success(client: TestClient):
    """Verify POST /api/v1/workflows/execute returns 200 OK and execution deliverables."""
    mock_executor_result = {
        "workflow_id": str(uuid.uuid4()),
        "status": WorkflowStatus.COMPLETED.value,
        "final_output": {
            "report_title": "Distributed Pipeline Architecture",
            "executive_summary": "Architecture completed and audited.",
            "sections": [],
            "conclusions": ["Production ready"],
            "missing_evidence": [],
        },
        "selected_agents": ["supervisor", "planner", "coder", "reviewer", "reporter"],
        "completed_agents": ["supervisor", "planner", "coder", "reviewer", "reporter"],
        "revision_count": 0,
        "review_result": {"verdict": "ACCEPT", "meets_requirements": True},
        "agent_results": {"coder": {"code": "class Pipeline: pass"}},
        "errors": [],
    }

    with patch(
        "app.orchestration.executor.WorkflowExecutor.execute_workflow",
        new_callable=AsyncMock,
        return_value=mock_executor_result,
    ):
        response = client.post(
            "/api/v1/workflows/execute",
            json={
                "user_request": "Architect a distributed multi-agent system on Kubernetes.",
                "max_revisions": 3,
            },
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "COMPLETED"
        assert data["final_output"]["report_title"] == "Distributed Pipeline Architecture"
        assert "coder" in data["completed_agents"]
        assert data["revision_count"] == 0


def test_execute_workflow_api_validation_error(client: TestClient):
    """Verify POST /api/v1/workflows/execute rejects empty user_request with 422."""
    response = client.post(
        "/api/v1/workflows/execute",
        json={"user_request": ""},
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_get_workflow_api_success(client: TestClient):
    """Verify GET /api/v1/workflows/{workflow_id} returns 200 OK for an existing workflow."""
    wf_id = uuid.uuid4()
    mock_workflow = Workflow(
        id=wf_id,
        name="Test Workflow",
        description="Verification workflow",
        status=WorkflowStatus.COMPLETED.value,
        input_data={"user_request": "Verify system"},
        result_data={"final_output": "OK"},
    )
    mock_workflow.created_at = datetime.now(UTC)
    mock_workflow.updated_at = datetime.now(UTC)

    with patch(
        "app.api.routes.workflows.get_workflow_by_id",
        new_callable=AsyncMock,
        return_value=mock_workflow,
    ):
        response = client.get(f"/api/v1/workflows/{wf_id}")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["id"] == str(wf_id)
        assert data["status"] == "COMPLETED"
        assert data["name"] == "Test Workflow"


def test_get_workflow_api_not_found(client: TestClient):
    """Verify GET /api/v1/workflows/{workflow_id} returns 404 for nonexistent UUID."""
    random_id = uuid.uuid4()
    with patch(
        "app.api.routes.workflows.get_workflow_by_id",
        new_callable=AsyncMock,
        return_value=None,
    ):
        response = client.get(f"/api/v1/workflows/{random_id}")
        assert response.status_code == status.HTTP_404_NOT_FOUND
        data = response.json()
        assert data["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_get_workflow_state_api(client: TestClient):
    """Verify GET /api/v1/workflows/{workflow_id}/state inspects graph checkpoint state."""
    wf_id = uuid.uuid4()
    with patch(
        "app.orchestration.checkpointer.WorkflowCheckpointAdapter.get_latest_checkpoint",
        new_callable=AsyncMock,
        return_value={"execution_status": "COMPLETED", "completed_agents": ["supervisor"]},
    ):
        response = client.get(f"/api/v1/workflows/{wf_id}/state")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["workflow_id"] == str(wf_id)
        assert data["has_checkpoint"] is True
        assert data["checkpoint_state"]["execution_status"] == "COMPLETED"


def test_get_workflow_tasks_api(client: TestClient):
    """Verify GET /api/v1/workflows/{workflow_id}/tasks returns list of subtask records."""
    wf_id = uuid.uuid4()
    t_id = uuid.uuid4()
    mock_task = Task(
        id=t_id,
        workflow_id=wf_id,
        name="Agent Execution: Coder",
        task_type="coder",
        description="Generated deliverables",
        status="COMPLETED",
        result_data={"code": "pass"},
    )
    mock_task.created_at = datetime.now(UTC)
    mock_task.updated_at = datetime.now(UTC)

    with patch(
        "app.api.routes.workflows.get_tasks_by_workflow_id",
        new_callable=AsyncMock,
        return_value=[mock_task],
    ):
        response = client.get(f"/api/v1/workflows/{wf_id}/tasks")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "Agent Execution: Coder"
        assert data[0]["task_type"] == "coder"
