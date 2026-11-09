"""Integration tests for LLM generation API endpoints."""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

from fastapi import status
from fastapi.testclient import TestClient

from app.core.exceptions import LLMAuthenticationError
from app.schemas.llm import SampleAnalysisSchema
from app.services.llm.base import LLMResponse, LLMUsage


def test_generate_text_api_success(client: TestClient):
    """Verify POST /api/v1/llm/generate returns 200 OK and expected generation payload."""
    mock_response = LLMResponse(
        content="Autonomous agents execute workflows asynchronously.",
        parsed=None,
        usage=LLMUsage(
            prompt_tokens=12,
            completion_tokens=8,
            total_tokens=20,
            duration_ms=450,
            estimated_cost_usd=Decimal("0.000003"),
            model_name="gemini-2.5-flash",
        ),
        model="gemini-2.5-flash",
        finish_reason="STOP",
    )

    with patch(
        "app.services.llm.service.LLMService.generate_text",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        response = client.post(
            "/api/v1/llm/generate",
            json={
                "prompt": "What is an autonomous agent?",
                "temperature": 0.2,
            },
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["content"] == "Autonomous agents execute workflows asynchronously."
        assert data["model"] == "gemini-2.5-flash"
        assert data["tokens_used"] == 20
        assert data["prompt_tokens"] == 12
        assert data["completion_tokens"] == 8
        assert data["duration_ms"] == 450
        assert data["estimated_cost_usd"] == "0.000003"


def test_generate_structured_api_success(client: TestClient):
    """Verify POST /api/v1/llm/generate-structured returns 200 OK with validated Pydantic schema."""
    parsed_sample = SampleAnalysisSchema(
        summary="Redis handles high-speed broker messaging.",
        key_findings=["Sub-millisecond latency", "In-memory architecture"],
        confidence_score=0.99,
    )

    mock_response = LLMResponse(
        content='{"summary": "..."}',
        parsed=parsed_sample,
        usage=LLMUsage(
            prompt_tokens=40,
            completion_tokens=30,
            total_tokens=70,
            duration_ms=620,
            estimated_cost_usd=Decimal("0.000012"),
            model_name="gemini-2.5-flash",
        ),
        model="gemini-2.5-flash",
        finish_reason="STOP",
    )

    with patch(
        "app.services.llm.service.LLMService.generate_structured",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        response = client.post(
            "/api/v1/llm/generate-structured",
            json={
                "prompt": "Analyze Redis message broker capabilities.",
            },
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["data"]["summary"] == "Redis handles high-speed broker messaging."
        assert len(data["data"]["key_findings"]) == 2
        assert data["data"]["confidence_score"] == 0.99
        assert data["tokens_used"] == 70
        assert data["model"] == "gemini-2.5-flash"


def test_generate_text_api_validation_error(client: TestClient):
    """Verify POST /api/v1/llm/generate rejects empty prompt with 422 Unprocessable Entity."""
    response = client.post(
        "/api/v1/llm/generate",
        json={"prompt": ""},
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_generate_text_api_auth_error_status_code(client: TestClient):
    """Verify POST /api/v1/llm/generate returns 401 Unauthorized when LLM auth fails."""
    with patch(
        "app.services.llm.service.LLMService.generate_text",
        new_callable=AsyncMock,
        side_effect=LLMAuthenticationError("Invalid API key."),
    ):
        response = client.post(
            "/api/v1/llm/generate",
            json={"prompt": "Test auth failure"},
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        data = response.json()
        assert data["error"]["code"] == "LLM_AUTHENTICATION_ERROR"
        assert "Invalid API key" in data["error"]["message"]
