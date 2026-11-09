"""Integration tests for Planning Engine API endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from fastapi import status
from fastapi.testclient import TestClient

from app.schemas.planning import (
    ExecutionPlan,
    ExecutionStage,
    PlannedTask,
    PlanStatus,
    TaskPriority,
)


def _build_sample_execution_plan(plan_id: str | None = None) -> ExecutionPlan:
    """Helper creating a sample ExecutionPlan for API mock responses."""
    pid = plan_id or str(uuid.uuid4())
    t1 = PlannedTask(
        task_id="task_1",
        name="Research DB",
        description="Study relational designs",
        task_type="research",
        recommended_agent_id="researcher",
        dependencies=[],
        priority=TaskPriority.HIGH,
        expected_output="DB Schema design",
    )
    t2 = PlannedTask(
        task_id="task_2",
        name="Implement Models",
        description="Write SQLAlchemy models",
        task_type="coding",
        recommended_agent_id="coder",
        dependencies=["task_1"],
        priority=TaskPriority.MEDIUM,
        expected_output="Python model code",
    )
    stage1 = ExecutionStage(
        stage_number=1, name="Stage 1: Research", tasks=[t1], can_run_parallel=False
    )
    stage2 = ExecutionStage(
        stage_number=2, name="Stage 2: Coding", tasks=[t2], can_run_parallel=False
    )

    return ExecutionPlan(
        plan_id=pid,
        objective="Design and implement database models",
        tasks=[t1, t2],
        execution_stages=[stage1, stage2],
        estimated_task_count=2,
        plan_status=PlanStatus.OPTIMIZED,
        validation_errors=[],
        metadata={"total_planning_duration_ms": 150},
        created_at=datetime.now(UTC),
    )


def test_generate_plan_api_success(client: TestClient):
    """Verify POST /api/v1/planning/generate returns 200 OK and valid ExecutionPlan."""
    sample_plan = _build_sample_execution_plan()

    with patch(
        "app.services.planning_service.PlanningService.generate_plan",
        new=AsyncMock(return_value=sample_plan),
    ):
        response = client.post(
            "/api/v1/planning/generate",
            json={
                "user_request": "Design and implement database models for task scheduling",
                "constraints": ["PostgreSQL", "AsyncIO"],
                "max_tasks": 5,
            },
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["plan_id"] == sample_plan.plan_id
        assert data["objective"] == "Design and implement database models"
        assert len(data["tasks"]) == 2
        assert len(data["execution_stages"]) == 2
        assert data["plan_status"] == "OPTIMIZED"


def test_generate_plan_api_validation_error(client: TestClient):
    """Verify POST /api/v1/planning/generate returns 422 for empty user_request."""
    response = client.post(
        "/api/v1/planning/generate",
        json={"user_request": ""},
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_get_plan_api_success(client: TestClient):
    """Verify GET /api/v1/planning/{plan_id} returns 200 and plan details."""
    plan_uuid = uuid.uuid4()
    sample_plan = _build_sample_execution_plan(plan_id=str(plan_uuid))

    with patch(
        "app.services.planning_service.PlanningService.get_plan_by_id",
        new=AsyncMock(return_value=sample_plan),
    ):
        response = client.get(f"/api/v1/planning/{plan_uuid}")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["plan_id"] == str(plan_uuid)
        assert data["estimated_task_count"] == 2


def test_get_plan_api_not_found(client: TestClient):
    """Verify GET /api/v1/planning/{plan_id} returns 404 for unknown plan."""
    unknown_id = uuid.uuid4()

    with patch(
        "app.services.planning_service.PlanningService.get_plan_by_id",
        new=AsyncMock(return_value=None),
    ):
        response = client.get(f"/api/v1/planning/{unknown_id}")
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert f"'{unknown_id}' was not found" in response.json()["detail"]


def test_get_plan_tasks_api(client: TestClient):
    """Verify GET /api/v1/planning/{plan_id}/tasks returns list of planned tasks."""
    plan_uuid = uuid.uuid4()
    sample_plan = _build_sample_execution_plan(plan_id=str(plan_uuid))

    with patch(
        "app.services.planning_service.PlanningService.get_plan_tasks",
        new=AsyncMock(return_value=sample_plan.tasks),
    ):
        response = client.get(f"/api/v1/planning/{plan_uuid}/tasks")
        assert response.status_code == status.HTTP_200_OK
        tasks = response.json()
        assert len(tasks) == 2
        assert tasks[0]["task_id"] == "task_1"
        assert tasks[1]["task_id"] == "task_2"


def test_get_plan_stages_api(client: TestClient):
    """Verify GET /api/v1/planning/{plan_id}/stages returns list of execution stages."""
    plan_uuid = uuid.uuid4()
    sample_plan = _build_sample_execution_plan(plan_id=str(plan_uuid))

    with patch(
        "app.services.planning_service.PlanningService.get_plan_stages",
        new=AsyncMock(return_value=sample_plan.execution_stages),
    ):
        response = client.get(f"/api/v1/planning/{plan_uuid}/stages")
        assert response.status_code == status.HTTP_200_OK
        stages = response.json()
        assert len(stages) == 2
        assert stages[0]["stage_number"] == 1
        assert stages[1]["stage_number"] == 2
