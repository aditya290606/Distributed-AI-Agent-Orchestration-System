"""Google Gemini API provider implementation using the official Google GenAI SDK."""

import asyncio
import logging
import random
import time
from typing import Any, TypeVar

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

from app.core.exceptions import (
    LLMAuthenticationError,
    LLMError,
    LLMInvalidRequestError,
    LLMRateLimitError,
    LLMStructuredOutputError,
    LLMTimeoutError,
)
from app.services.llm.base import BaseLLMProvider, LLMResponse, LLMUsage
from app.services.llm.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError
from app.services.llm.cost import calculate_token_cost

logger = logging.getLogger("app.services.llm.gemini")

T = TypeVar("T", bound=BaseModel)


class GeminiProvider(BaseLLMProvider):
    """Asynchronous Gemini LLM Provider leveraging official google-genai SDK."""

    def __init__(
        self,
        api_key: str,
        model_name: str = "gemini-2.5-flash",
        timeout_seconds: float = 60.0,
        max_retries: int = 3,
        temperature: float = 0.2,
        max_output_tokens: int = 4096,
        client: genai.Client | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self.api_key = api_key
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.default_temperature = temperature
        self.default_max_output_tokens = max_output_tokens
        self.circuit_breaker = circuit_breaker or CircuitBreaker()

        # Allow passing an existing client (vital for testing and mock injection)
        if client is not None:
            self._client = client
        elif api_key and api_key.strip():
            self._client = genai.Client(api_key=api_key)
        else:
            self._client = None

    def _ensure_client(self) -> genai.Client:
        """Verify client is configured and return it, raising LLMAuthenticationError if missing."""
        if self._client is None:
            if not self.api_key or not self.api_key.strip():
                raise LLMAuthenticationError(
                    message="Gemini API Key is not configured. Set GEMINI_API_KEY in .env.",
                    model=self.model_name,
                )
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def get_model_name(self) -> str:
        """Return the active model name."""
        return self.model_name

    def _build_config(
        self,
        system_instruction: str | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        response_schema: type[BaseModel] | None = None,
    ) -> types.GenerateContentConfig:
        """Construct official GenAI GenerateContentConfig options."""
        cfg_kwargs: dict[str, Any] = {
            "temperature": temperature if temperature is not None else self.default_temperature,
            "max_output_tokens": max_output_tokens
            if max_output_tokens is not None
            else self.default_max_output_tokens,
        }
        if system_instruction:
            cfg_kwargs["system_instruction"] = system_instruction

        if response_schema:
            cfg_kwargs["response_mime_type"] = "application/json"
            cfg_kwargs["response_schema"] = response_schema

        return types.GenerateContentConfig(**cfg_kwargs)

    def _parse_usage(self, response: Any, duration_ms: int) -> LLMUsage:
        """Extract usage metadata and calculate financial cost."""
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0

        if hasattr(response, "usage_metadata") and response.usage_metadata is not None:
            usage_meta = response.usage_metadata
            prompt_tokens = getattr(usage_meta, "prompt_token_count", 0) or 0
            completion_tokens = getattr(usage_meta, "candidates_token_count", 0) or 0
            total_tokens = getattr(usage_meta, "total_token_count", 0) or (
                prompt_tokens + completion_tokens
            )

        cost = calculate_token_cost(self.model_name, prompt_tokens, completion_tokens)

        return LLMUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            duration_ms=duration_ms,
            estimated_cost_usd=cost,
            model_name=self.model_name,
        )

    async def _execute_with_retry(
        self,
        contents: str,
        config: types.GenerateContentConfig,
    ) -> tuple[Any, int]:
        """Execute async model call with bounded retries and exponential backoff.

        Never retries on permanent authentication errors (401/403) or bad requests (400).
        """
        client = self._ensure_client()
        start_time = time.perf_counter()

        for attempt in range(self.max_retries + 1):
            try:
                # Enforce per-request timeout ceiling and circuit breaker protection
                async with asyncio.timeout(self.timeout_seconds):
                    response = await self.circuit_breaker.execute(
                        client.aio.models.generate_content,
                        model=self.model_name,
                        contents=contents,
                        config=config,
                    )
                duration_ms = int((time.perf_counter() - start_time) * 1000)
                return response, duration_ms

            except CircuitBreakerOpenError:
                # Circuit breaker is OPEN: fail fast immediately without burning retries
                raise

            except TimeoutError as exc:
                if attempt >= self.max_retries:
                    raise LLMTimeoutError(
                        message=f"Gemini request timed out after {self.timeout_seconds}s across {self.max_retries + 1} attempts.",
                        model=self.model_name,
                        timeout_seconds=self.timeout_seconds,
                    ) from exc
                delay = (2.0**attempt) + random.uniform(0.1, 0.5)
                logger.warning(
                    "Gemini timeout on attempt %d/%d. Retrying in %.2fs...",
                    attempt + 1,
                    self.max_retries,
                    delay,
                )
                await asyncio.sleep(delay)

            except errors.APIError as exc:
                status_code = getattr(exc, "code", None)

                # Permanent client/auth failures: DO NOT RETRY
                if status_code in (401, 403):
                    raise LLMAuthenticationError(
                        message=f"Gemini authentication failed ({status_code}): {exc.message}",
                        model=self.model_name,
                    ) from exc
                if status_code == 400:
                    raise LLMInvalidRequestError(
                        message=f"Gemini invalid request ({status_code}): {exc.message}",
                        model=self.model_name,
                    ) from exc

                # Transient rate-limit or internal errors: RETRY WITH BACKOFF
                if attempt >= self.max_retries:
                    if status_code == 429:
                        raise LLMRateLimitError(
                            message=f"Gemini rate limit exceeded after {self.max_retries} retries: {exc.message}",
                            model=self.model_name,
                        ) from exc
                    raise LLMError(
                        message=f"Gemini API error ({status_code}) exhausted retries: {exc.message}",
                        model=self.model_name,
                    ) from exc

                delay = (2.0**attempt) + random.uniform(0.2, 0.8)
                logger.warning(
                    "Gemini transient error %s on attempt %d/%d. Retrying in %.2fs...",
                    status_code,
                    attempt + 1,
                    self.max_retries,
                    delay,
                )
                await asyncio.sleep(delay)

            except Exception as exc:
                raise LLMError(
                    message=f"Unexpected failure during Gemini invocation: {exc!s}",
                    model=self.model_name,
                ) from exc

        raise LLMError("Exhausted all retry attempts without a response", model=self.model_name)

    async def generate_text(
        self,
        prompt: str,
        system_instruction: str | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> LLMResponse[None]:
        """Generate unstructured text from a prompt."""
        config = self._build_config(
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )

        response, duration_ms = await self._execute_with_retry(contents=prompt, config=config)
        usage = self._parse_usage(response, duration_ms)

        raw_text = getattr(response, "text", "") or ""
        finish_reason = None
        if hasattr(response, "candidates") and response.candidates:
            finish_reason = getattr(response.candidates[0], "finish_reason", None)

        return LLMResponse(
            content=raw_text,
            parsed=None,
            usage=usage,
            model=self.model_name,
            finish_reason=str(finish_reason) if finish_reason else None,
        )

    async def generate_structured(
        self,
        prompt: str,
        response_model: type[T],
        system_instruction: str | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> LLMResponse[T]:
        """Generate structured model output validating strictly against a Pydantic schema."""
        config = self._build_config(
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            response_schema=response_model,
        )

        response, duration_ms = await self._execute_with_retry(contents=prompt, config=config)
        usage = self._parse_usage(response, duration_ms)

        raw_text = getattr(response, "text", "") or ""
        finish_reason = None
        if hasattr(response, "candidates") and response.candidates:
            finish_reason = getattr(response.candidates[0], "finish_reason", None)

        # Validate structured JSON response against Pydantic schema
        try:
            parsed_instance = response_model.model_validate_json(raw_text)
        except (ValidationError, ValueError) as exc:
            logger.error(
                "Structured output validation failed: %s | Raw response: %s",
                str(exc),
                raw_text[:200],
            )
            raise LLMStructuredOutputError(
                message=f"Model output failed Pydantic schema validation for '{response_model.__name__}'",
                raw_response=raw_text,
                validation_error=str(exc),
                model=self.model_name,
            ) from exc

        return LLMResponse(
            content=raw_text,
            parsed=parsed_instance,
            usage=usage,
            model=self.model_name,
            finish_reason=str(finish_reason) if finish_reason else None,
        )
