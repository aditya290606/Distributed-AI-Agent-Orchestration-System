"""Centralized LLM service providing a unified interface for all NEXUS AI agents."""

import logging
import uuid
from typing import Any, TypeVar

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import LLMInvalidRequestError
from app.db.models.llm_usage import LLMUsageCost
from app.services.llm.base import BaseLLMProvider, LLMResponse
from app.services.llm.gemini_provider import GeminiProvider

logger = logging.getLogger("app.services.llm.service")

T = TypeVar("T", bound=BaseModel)

_llm_service_instance: "LLMService | None" = None


class LLMService:
    """Centralized LLM orchestration service for text and structured generation."""

    def __init__(
        self,
        provider: BaseLLMProvider | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.provider = provider or GeminiProvider(
            api_key=self.settings.gemini_api_key,
            model_name=self.settings.gemini_model,
            timeout_seconds=self.settings.gemini_timeout,
            max_retries=self.settings.gemini_max_retries,
            temperature=self.settings.gemini_temperature,
            max_output_tokens=self.settings.gemini_max_output_tokens,
        )

    def _validate_request_params(
        self,
        prompt: str,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> None:
        """Validate input prompt and parameter boundaries."""
        if not prompt or not prompt.strip():
            raise LLMInvalidRequestError(
                message="Prompt cannot be empty or whitespace.",
                model=self.provider.get_model_name(),
            )
        if temperature is not None and not (0.0 <= temperature <= 2.0):
            raise LLMInvalidRequestError(
                message=f"Temperature must be between 0.0 and 2.0 (got {temperature}).",
                model=self.provider.get_model_name(),
            )
        if max_output_tokens is not None and max_output_tokens <= 0:
            raise LLMInvalidRequestError(
                message=f"max_output_tokens must be greater than 0 (got {max_output_tokens}).",
                model=self.provider.get_model_name(),
            )

    async def _record_usage_in_db(
        self,
        session: AsyncSession | None,
        response: LLMResponse[Any],
        workflow_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        error_type: str | None = None,
    ) -> None:
        """Persist invocation usage metrics into database if an active session is provided."""
        if session is None:
            return

        try:
            record = LLMUsageCost(
                workflow_id=workflow_id,
                task_id=task_id,
                model_name=response.model,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                total_tokens=response.usage.total_tokens,
                duration_ms=response.usage.duration_ms,
                is_success=error_type is None,
                error_type=error_type,
                estimated_cost_usd=response.usage.estimated_cost_usd,
            )
            session.add(record)
            await session.flush()
        except Exception as exc:
            logger.warning("Failed to persist LLM usage record to database: %s", str(exc))

    async def generate_text(
        self,
        prompt: str,
        system_instruction: str | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        session: AsyncSession | None = None,
        workflow_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
    ) -> LLMResponse[None]:
        """Generate unstructured text with parameter validation and optional DB usage recording."""
        self._validate_request_params(prompt, temperature, max_output_tokens)

        logger.info(
            "Generating text with model=%s (prompt_len=%d)",
            self.provider.get_model_name(),
            len(prompt),
        )

        response = await self.provider.generate_text(
            prompt=prompt,
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )

        logger.info(
            "Text generation complete: model=%s tokens=%d duration=%dms cost=%s",
            response.model,
            response.usage.total_tokens,
            response.usage.duration_ms,
            response.usage.estimated_cost_usd,
        )

        await self._record_usage_in_db(
            session=session,
            response=response,
            workflow_id=workflow_id,
            task_id=task_id,
        )

        return response

    async def generate_structured(
        self,
        prompt: str,
        response_model: type[T],
        system_instruction: str | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        session: AsyncSession | None = None,
        workflow_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
    ) -> LLMResponse[T]:
        """Generate structured Pydantic object with validation and optional DB usage recording."""
        self._validate_request_params(prompt, temperature, max_output_tokens)

        logger.info(
            "Generating structured output for schema=%s with model=%s",
            response_model.__name__,
            self.provider.get_model_name(),
        )

        response = await self.provider.generate_structured(
            prompt=prompt,
            response_model=response_model,
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )

        logger.info(
            "Structured generation complete: schema=%s tokens=%d duration=%dms cost=%s",
            response_model.__name__,
            response.usage.total_tokens,
            response.usage.duration_ms,
            response.usage.estimated_cost_usd,
        )

        await self._record_usage_in_db(
            session=session,
            response=response,
            workflow_id=workflow_id,
            task_id=task_id,
        )

        return response


def get_llm_service(settings: Settings = Depends(get_settings)) -> LLMService:
    """Return a singleton instance of the LLMService."""
    global _llm_service_instance
    if not isinstance(settings, Settings):
        settings = get_settings()
    if _llm_service_instance is None:
        _llm_service_instance = LLMService(settings=settings)
    return _llm_service_instance
