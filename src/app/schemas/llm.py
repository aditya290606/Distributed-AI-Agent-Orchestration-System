"""Pydantic schemas for LLM text and structured generation API endpoints."""

from decimal import Decimal

from pydantic import BaseModel, Field


class GenerateTextRequest(BaseModel):
    """Input payload for text generation endpoint."""

    prompt: str = Field(
        min_length=1,
        max_length=10000,
        description="The prompt text to send to the model",
        examples=[
            "Explain the difference between synchronous and asynchronous architectures in three bullet points."
        ],
    )
    system_instruction: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional system instruction guiding tone and constraints",
    )
    temperature: float | None = Field(
        default=None,
        ge=0.0,
        le=2.0,
        description="Sampling temperature (lower = more deterministic, higher = more creative)",
    )
    max_output_tokens: int | None = Field(
        default=None,
        gt=0,
        le=8192,
        description="Maximum tokens allowed in completion",
    )


class GenerateTextResponse(BaseModel):
    """Output payload for text generation endpoint."""

    content: str = Field(description="Generated model text content")
    model: str = Field(description="Model used for generation")
    tokens_used: int = Field(description="Total token consumption")
    prompt_tokens: int = Field(description="Prompt token count")
    completion_tokens: int = Field(description="Output token count")
    duration_ms: int = Field(description="Request execution latency in milliseconds")
    estimated_cost_usd: Decimal | None = Field(
        default=None,
        description="Calculated financial cost in USD, or null if unpriced",
    )


class SampleAnalysisSchema(BaseModel):
    """Example structured output schema representing an analytical synthesis."""

    summary: str = Field(
        description="Executive summary of the topic or document",
    )
    key_findings: list[str] = Field(
        default_factory=list,
        description="Bullet-point list of critical findings or insights",
    )
    confidence_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Self-evaluated confidence score between 0.0 and 1.0",
    )


class GenerateStructuredRequest(BaseModel):
    """Input payload for structured output generation endpoint."""

    prompt: str = Field(
        min_length=1,
        max_length=10000,
        description="The input context to analyze and structure",
        examples=[
            "Analyze the benefits of using Celery and Redis for distributed background AI workflows."
        ],
    )
    system_instruction: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional system instructions for the analysis",
    )
    temperature: float | None = Field(
        default=None,
        ge=0.0,
        le=2.0,
        description="Sampling temperature",
    )
    max_output_tokens: int | None = Field(
        default=None,
        gt=0,
        le=8192,
        description="Maximum tokens in generated response",
    )


class GenerateStructuredResponse(BaseModel):
    """Output payload containing parsed structured data and usage telemetry."""

    data: SampleAnalysisSchema = Field(
        description="Model output parsed and validated into the Pydantic schema",
    )
    model: str = Field(description="Model used for generation")
    tokens_used: int = Field(description="Total token consumption")
    duration_ms: int = Field(description="Request latency in milliseconds")
    estimated_cost_usd: Decimal | None = Field(
        default=None,
        description="Estimated cost in USD",
    )
