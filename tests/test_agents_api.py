"""Integration tests for Specialized Agent API endpoints."""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

from fastapi import status
from fastapi.testclient import TestClient

from app.agents.contracts import AgentExecutionStatus
from app.agents.specialized.schemas import SupervisorResult
from app.services.llm.base import LLMResponse, LLMUsage


def test_list_agents_api(client: TestClient):
    """Verify GET /api/v1/agents returns 200 OK and all registered specialized agents."""
    response = client.get("/api/v1/agents")
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert "agents" in data
    assert "total_count" in data
    assert data["total_count"] >= 7

    agent_ids = {agent["agent_id"] for agent in data["agents"]}
    expected_agents = {
        "supervisor",
        "planner",
        "researcher",
        "analyst",
        "coder",
        "reviewer",
        "reporter",
    }
    assert expected_agents.issubset(agent_ids)


def test_get_agent_details_api_success(client: TestClient):
    """Verify GET /api/v1/agents/{agent_id} returns 200 OK for a valid agent."""
    response = client.get("/api/v1/agents/coder")
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert data["agent_id"] == "coder"
    assert data["name"] == "Coding Agent"
    assert "Software Implementation" in data["role"]
    assert data["output_schema_name"] == "CodingResult"


def test_get_agent_details_api_not_found(client: TestClient):
    """Verify GET /api/v1/agents/{agent_id} returns 404 Not Found for unknown agent."""
    response = client.get("/api/v1/agents/unknown_agent_999")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    data = response.json()
    assert data["error"]["code"] == "AGENT_NOT_FOUND"


def test_execute_agent_api_success(client: TestClient):
    """Verify POST /api/v1/agents/{agent_id}/execute returns 200 OK and execution deliverables."""
    mock_result = SupervisorResult(
        task_classification="system_design",
        complexity_assessment="high",
        required_capabilities=["architecture", "planning"],
        recommended_agents=["planner", "coder", "reviewer"],
        delegation_strategy="Planner decomposes, Coder builds, Reviewer audits.",
        reasoning_summary="Distributed architecture task requiring decomposition and testing.",
    )

    mock_llm_response = LLMResponse(
        content="{}",
        parsed=mock_result,
        usage=LLMUsage(
            prompt_tokens=45,
            completion_tokens=85,
            total_tokens=130,
            duration_ms=480,
            estimated_cost_usd=Decimal("0.000028"),
            model_name="gemini-2.5-flash",
        ),
        model="gemini-2.5-flash",
    )

    with patch(
        "app.services.llm.service.LLMService.generate_structured",
        new_callable=AsyncMock,
        return_value=mock_llm_response,
    ):
        response = client.post(
            "/api/v1/agents/supervisor/execute",
            json={
                "task_description": "Architect a distributed multi-agent system on Kubernetes.",
                "context": {"target_cloud": "GCP"},
                "temperature": 0.2,
            },
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["agent_id"] == "supervisor"
        assert data["agent_name"] == "Supervisor Agent"
        assert data["status"] == AgentExecutionStatus.SUCCESS.value
        assert data["result"]["task_classification"] == "system_design"
        assert data["result"]["recommended_agents"] == ["planner", "coder", "reviewer"]
        assert "Distributed architecture task" in data["reasoning_summary"]
        assert data["execution_metadata"]["tokens_used"] == 130


def test_execute_agent_api_validation_error(client: TestClient):
    """Verify POST /api/v1/agents/{agent_id}/execute rejects empty task_description with 422."""
    response = client.post(
        "/api/v1/agents/coder/execute",
        json={"task_description": ""},
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_execute_agent_api_not_found(client: TestClient):
    """Verify POST /api/v1/agents/{agent_id}/execute returns 404 for unknown agent."""
    response = client.post(
        "/api/v1/agents/nonexistent_agent/execute",
        json={"task_description": "Valid task description"},
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND
    data = response.json()
    assert data["error"]["code"] == "AGENT_NOT_FOUND"
