"""Unit tests for Gemini configuration, provider, prompts, cost calculation, and LLM service."""

import asyncio
import os
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.genai import errors

from app.core.config import Settings
from app.core.exceptions import (
    LLMAuthenticationError,
    LLMInvalidRequestError,
    LLMStructuredOutputError,
)
from app.schemas.llm import SampleAnalysisSchema
from app.services.llm.base import BaseLLMProvider
from app.services.llm.cost import calculate_token_cost
from app.services.llm.gemini_provider import GeminiProvider
from app.services.llm.prompts import PromptTemplate
from app.services.llm.service import LLMService

# ------------------------------------------------------------------------------
# 1. Configuration & Cost Tests
# ------------------------------------------------------------------------------


def test_gemini_configuration_defaults():
    """Verify Gemini configuration initializes with documented defaults."""
    settings = Settings()
    assert settings.gemini_model == "gemini-2.5-flash"
    assert settings.gemini_timeout == 60.0
    assert settings.gemini_max_retries == 3
    assert settings.gemini_max_output_tokens == 4096
    assert settings.gemini_temperature == 0.2
    assert not settings.has_gemini_key


def test_gemini_configuration_custom_overrides():
    """Verify Gemini configuration overrides from environment parameters."""
    settings = Settings(
        GEMINI_API_KEY="test-secret-key-12345",
        GEMINI_MODEL="gemini-2.5-pro",
        GEMINI_TIMEOUT=45.0,
        GEMINI_MAX_RETRIES=5,
        GEMINI_MAX_OUTPUT_TOKENS=8192,
        GEMINI_TEMPERATURE=0.7,
    )
    assert settings.has_gemini_key
    assert settings.gemini_model == "gemini-2.5-pro"
    assert settings.gemini_timeout == 45.0
    assert settings.gemini_max_retries == 5
    assert settings.gemini_max_output_tokens == 8192
    assert settings.gemini_temperature == 0.7


def test_token_cost_calculation():
    """Verify official pricing calculation for Gemini 2.5 models and unpriced fallback."""
    # Gemini 2.5 Flash: $0.075 / 1M input, $0.30 / 1M output
    cost_flash = calculate_token_cost(
        model_name="gemini-2.5-flash",
        prompt_tokens=100_000,
        completion_tokens=20_000,
    )
    assert cost_flash is not None
    # 100k input = $0.0075, 20k output = $0.0060 -> Total $0.013500
    assert cost_flash == Decimal("0.013500")

    # Gemini 2.5 Pro: $1.25 / 1M input, $5.00 / 1M output
    cost_pro = calculate_token_cost(
        model_name="models/gemini-2.5-pro",
        prompt_tokens=10_000,
        completion_tokens=2_000,
    )
    assert cost_pro is not None
    # 10k input = $0.0125, 2k output = $0.0100 -> Total $0.022500
    assert cost_pro == Decimal("0.022500")

    # Unrecognized / custom model: must return None (never fabricate prices)
    unknown_cost = calculate_token_cost(
        model_name="custom-finetuned-unrecognized-model",
        prompt_tokens=1000,
        completion_tokens=500,
    )
    assert unknown_cost is None


# ------------------------------------------------------------------------------
# 2. Prompt Management Tests
# ------------------------------------------------------------------------------


def test_prompt_template_variable_extraction_and_formatting():
    """Verify PromptTemplate extracts variables, formats content, and rejects missing keys."""
    template = PromptTemplate(
        name="test_template",
        system_instruction="Persona: {role}",
        template="Analyze this topic: {topic} within {timeframe}.",
    )
    assert template.required_variables == {"role", "topic", "timeframe"}

    prompt, system = template.format(
        role="Lead Researcher",
        topic="Multi-Agent Systems",
        timeframe="2026",
    )
    assert prompt == "Analyze this topic: Multi-Agent Systems within 2026."
    assert system == "Persona: Lead Researcher"

    # Missing variables must raise ValueError
    with pytest.raises(ValueError, match="missing required variables"):
        template.format(role="Lead Researcher", topic="Multi-Agent Systems")


# ------------------------------------------------------------------------------
# 3. Gemini Provider & Client Tests
# ------------------------------------------------------------------------------


def test_gemini_provider_unconfigured_auth_error():
    """Verify GeminiProvider raises LLMAuthenticationError when no API key is provided."""
    provider = GeminiProvider(api_key="")
    assert provider.get_model_name() == "gemini-2.5-flash"

    def _run():
        with pytest.raises(LLMAuthenticationError, match="API Key is not configured"):
            asyncio.run(provider.generate_text("Hello"))

    _run()


def test_gemini_provider_text_generation_with_mock():
    """Verify generate_text calls official SDK, parses usage, and returns LLMResponse."""
    mock_genai_client = MagicMock()
    mock_aio_models = AsyncMock()
    mock_genai_client.aio.models = mock_aio_models

    mock_response = MagicMock()
    mock_response.text = "Distributed multi-agent systems coordinate autonomous tasks."
    mock_response.usage_metadata.prompt_token_count = 150
    mock_response.usage_metadata.candidates_token_count = 35
    mock_response.usage_metadata.total_token_count = 185
    mock_response.candidates = [MagicMock(finish_reason="STOP")]
    mock_aio_models.generate_content.return_value = mock_response

    provider = GeminiProvider(
        api_key="mock-test-key",
        model_name="gemini-2.5-flash",
        client=mock_genai_client,
    )

    async def _run():
        res = await provider.generate_text(
            prompt="Explain agent orchestration.",
            system_instruction="You are a system architect.",
            temperature=0.3,
        )
        assert res.content == "Distributed multi-agent systems coordinate autonomous tasks."
        assert res.model == "gemini-2.5-flash"
        assert res.usage.prompt_tokens == 150
        assert res.usage.completion_tokens == 35
        assert res.usage.total_tokens == 185
        assert res.usage.estimated_cost_usd is not None
        assert res.finish_reason == "STOP"
        mock_aio_models.generate_content.assert_awaited_once()

    asyncio.run(_run())


def test_gemini_provider_structured_output_with_mock():
    """Verify generate_structured requests JSON schema and validates output into Pydantic model."""
    mock_genai_client = MagicMock()
    mock_aio_models = AsyncMock()
    mock_genai_client.aio.models = mock_aio_models

    json_payload = (
        '{"summary": "Celery isolates background execution.", '
        '"key_findings": ["Process isolation", "Fault tolerance"], '
        '"confidence_score": 0.98}'
    )
    mock_response = MagicMock()
    mock_response.text = json_payload
    mock_response.usage_metadata.prompt_token_count = 200
    mock_response.usage_metadata.candidates_token_count = 45
    mock_response.usage_metadata.total_token_count = 245
    mock_response.candidates = [MagicMock(finish_reason="STOP")]
    mock_aio_models.generate_content.return_value = mock_response

    provider = GeminiProvider(
        api_key="mock-test-key",
        model_name="gemini-2.5-flash",
        client=mock_genai_client,
    )

    async def _run():
        res = await provider.generate_structured(
            prompt="Analyze Celery architecture.",
            response_model=SampleAnalysisSchema,
        )
        assert isinstance(res.parsed, SampleAnalysisSchema)
        assert res.parsed.summary == "Celery isolates background execution."
        assert len(res.parsed.key_findings) == 2
        assert res.parsed.confidence_score == 0.98
        assert res.usage.total_tokens == 245

    asyncio.run(_run())


def test_gemini_provider_structured_output_validation_error():
    """Verify generate_structured raises LLMStructuredOutputError when output schema is malformed."""
    mock_genai_client = MagicMock()
    mock_aio_models = AsyncMock()
    mock_genai_client.aio.models = mock_aio_models

    # Output missing 'confidence_score' and having wrong types
    mock_response = MagicMock()
    mock_response.text = '{"summary": "Incomplete data"}'
    mock_response.usage_metadata.prompt_token_count = 50
    mock_response.usage_metadata.candidates_token_count = 10
    mock_response.usage_metadata.total_token_count = 60
    mock_response.candidates = [MagicMock(finish_reason="STOP")]
    mock_aio_models.generate_content.return_value = mock_response

    provider = GeminiProvider(
        api_key="mock-test-key",
        client=mock_genai_client,
    )

    async def _run():
        with pytest.raises(LLMStructuredOutputError, match="failed Pydantic schema validation"):
            await provider.generate_structured(
                prompt="Analyze data.",
                response_model=SampleAnalysisSchema,
            )

    asyncio.run(_run())


def test_gemini_provider_permanent_error_no_retry():
    """Verify permanent 401 auth or 400 invalid request errors fail immediately without retry."""
    mock_genai_client = MagicMock()
    mock_aio_models = AsyncMock()
    mock_genai_client.aio.models = mock_aio_models

    # Simulate 401 Unauthorized
    api_err = errors.APIError(
        401, "API_KEY_INVALID", "API key not valid. Please pass a valid API key."
    )
    mock_aio_models.generate_content.side_effect = api_err

    provider = GeminiProvider(
        api_key="invalid-key",
        max_retries=3,
        client=mock_genai_client,
    )

    async def _run():
        with pytest.raises(LLMAuthenticationError, match="Gemini authentication failed"):
            await provider.generate_text("Test")
        # Exactly 1 call (no retries)
        assert mock_aio_models.generate_content.await_count == 1

    asyncio.run(_run())


def test_gemini_provider_retry_on_transient_error():
    """Verify transient 429 rate limit errors retry with backoff and succeed."""
    mock_genai_client = MagicMock()
    mock_aio_models = AsyncMock()
    mock_genai_client.aio.models = mock_aio_models

    # Attempt 1: 429 Rate limit, Attempt 2: Success
    rate_err = errors.APIError(429, "RESOURCE_EXHAUSTED", "Rate limit exceeded.")
    mock_success = MagicMock()
    mock_success.text = "Success on retry"
    mock_success.usage_metadata.prompt_token_count = 20
    mock_success.usage_metadata.candidates_token_count = 5
    mock_success.usage_metadata.total_token_count = 25
    mock_success.candidates = [MagicMock(finish_reason="STOP")]

    mock_aio_models.generate_content.side_effect = [rate_err, mock_success]

    provider = GeminiProvider(
        api_key="valid-key",
        max_retries=2,
        client=mock_genai_client,
    )

    async def _run():
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            res = await provider.generate_text("Test transient")
            assert res.content == "Success on retry"
            assert mock_aio_models.generate_content.await_count == 2
            mock_sleep.assert_awaited_once()

    asyncio.run(_run())


# ------------------------------------------------------------------------------
# 4. LLM Service Validation Tests
# ------------------------------------------------------------------------------


def test_llm_service_input_validation():
    """Verify LLMService validates prompt length and generation parameters before provider call."""
    mock_provider = MagicMock(spec=BaseLLMProvider)
    mock_provider.get_model_name.return_value = "gemini-2.5-flash"
    service = LLMService(provider=mock_provider)

    async def _run():
        # Empty prompt
        with pytest.raises(LLMInvalidRequestError, match="Prompt cannot be empty"):
            await service.generate_text(prompt="   ")

        # Temperature out of bounds
        with pytest.raises(
            LLMInvalidRequestError, match=r"Temperature must be between 0\.0 and 2\.0"
        ):
            await service.generate_text(prompt="Valid", temperature=2.5)

        # max_output_tokens <= 0
        with pytest.raises(
            LLMInvalidRequestError, match="max_output_tokens must be greater than 0"
        ):
            await service.generate_text(prompt="Valid", max_output_tokens=0)

    asyncio.run(_run())


# ------------------------------------------------------------------------------
# 5. Optional Manual Live Integration Test (Skipped by default in CI)
# ------------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.getenv("GEMINI_API_KEY"),
    reason="Requires valid GEMINI_API_KEY environment variable for live execution",
)
def test_live_gemini_text_and_structured_generation():
    """Manual live test verifying Google GenAI SDK connection and structured output against Gemini API."""
    api_key = os.environ["GEMINI_API_KEY"]
    provider = GeminiProvider(api_key=api_key, model_name="gemini-2.5-flash")

    async def _run():
        # Text generation
        res_text = await provider.generate_text(
            prompt="Respond in exactly three words: 'NEXUS AI Operational'",
            temperature=0.0,
        )
        assert res_text.content is not None
        assert res_text.usage.total_tokens > 0

        # Structured generation
        res_struct = await provider.generate_structured(
            prompt="Provide an analysis of the benefits of asynchronous Python for high-throughput APIs.",
            response_model=SampleAnalysisSchema,
            temperature=0.1,
        )
        assert isinstance(res_struct.parsed, SampleAnalysisSchema)
        assert res_struct.parsed.summary is not None
        assert 0.0 <= res_struct.parsed.confidence_score <= 1.0

    asyncio.run(_run())
