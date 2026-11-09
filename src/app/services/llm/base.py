"""Abstract provider interface and response contracts for LLM services."""

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T", bound=BaseModel)


class LLMUsage(BaseModel):
    """Token consumption, latency, and financial metrics for an LLM invocation."""

    prompt_tokens: int = Field(default=0, description="Input tokens processed")
    completion_tokens: int = Field(default=0, description="Generated output tokens")
    total_tokens: int = Field(default=0, description="Total tokens consumed")
    duration_ms: int = Field(default=0, description="Round-trip request duration in milliseconds")
    estimated_cost_usd: Decimal | None = Field(
        default=None,
        description="Estimated inference cost in USD, or None if unpriced",
    )
    model_name: str = Field(description="Model identifier that produced the response")


class LLMResponse(BaseModel, Generic[T]):
    """Standardized response container returned by all LLM providers."""

    content: str = Field(description="Raw text content produced by the model")
    parsed: T | None = Field(
        default=None,
        description="Validated Pydantic model instance if structured output was requested",
    )
    usage: LLMUsage = Field(description="Execution usage and telemetry metrics")
    model: str = Field(description="Model identifier that generated the response")
    finish_reason: str | None = Field(
        default=None,
        description="Completion stop reason (e.g. STOP, MAX_TOKENS)",
    )


class BaseLLMProvider(ABC):
    """Abstract interface defining standard operations for LLM providers."""

    @abstractmethod
    async def generate_text(
        self,
        prompt: str,
        system_instruction: str | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> LLMResponse[None]:
        """Generate unstructured text from a prompt.

        Args:
            prompt: User input or formatted template string.
            system_instruction: Optional system-level persona or constraint instructions.
            temperature: Sampling temperature override (0.0 to 2.0).
            max_output_tokens: Maximum tokens in generated completion.

        Returns:
            LLMResponse containing raw content and usage metrics.
        """
        pass

    @abstractmethod
    async def generate_structured(
        self,
        prompt: str,
        response_model: type[T],
        system_instruction: str | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> LLMResponse[T]:
        """Generate structured output adhering strictly to a Pydantic schema.

        Args:
            prompt: User input prompt.
            response_model: Pydantic model class to validate and parse output into.
            system_instruction: Optional system instruction.
            temperature: Sampling temperature override.
            max_output_tokens: Maximum tokens in generated completion.

        Returns:
            LLMResponse with parsed containing an instance of response_model.
        """
        pass

    @abstractmethod
    def get_model_name(self) -> str:
        """Return the active model name for this provider."""
        pass
