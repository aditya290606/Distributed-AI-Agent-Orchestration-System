"""LLM text generation and structured output API endpoints."""

import logging

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.dependencies import get_db
from app.schemas.llm import (
    GenerateStructuredRequest,
    GenerateStructuredResponse,
    GenerateTextRequest,
    GenerateTextResponse,
    SampleAnalysisSchema,
)
from app.services.llm.service import LLMService, get_llm_service

logger = logging.getLogger("app.api.routes.llm")

router = APIRouter(prefix="/llm", tags=["LLM Services"])


@router.post(
    "/generate",
    response_model=GenerateTextResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate Unstructured Text",
    description="Invokes the configured Gemini model to generate natural language text from a prompt.",
)
async def generate_text_endpoint(
    payload: GenerateTextRequest,
    llm_service: LLMService = Depends(get_llm_service),
    db: AsyncSession = Depends(get_db),
) -> GenerateTextResponse:
    """Execute text generation and record token telemetry."""
    response = await llm_service.generate_text(
        prompt=payload.prompt,
        system_instruction=payload.system_instruction,
        temperature=payload.temperature,
        max_output_tokens=payload.max_output_tokens,
        session=db,
    )

    return GenerateTextResponse(
        content=response.content,
        model=response.model,
        tokens_used=response.usage.total_tokens,
        prompt_tokens=response.usage.prompt_tokens,
        completion_tokens=response.usage.completion_tokens,
        duration_ms=response.usage.duration_ms,
        estimated_cost_usd=response.usage.estimated_cost_usd,
    )


@router.post(
    "/generate-structured",
    response_model=GenerateStructuredResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate Structured Output (Pydantic)",
    description="Invokes the Gemini model enforcing a strict Pydantic JSON schema (SampleAnalysisSchema).",
)
async def generate_structured_endpoint(
    payload: GenerateStructuredRequest,
    llm_service: LLMService = Depends(get_llm_service),
    db: AsyncSession = Depends(get_db),
) -> GenerateStructuredResponse:
    """Execute structured output generation with strict Pydantic validation."""
    response = await llm_service.generate_structured(
        prompt=payload.prompt,
        response_model=SampleAnalysisSchema,
        system_instruction=payload.system_instruction,
        temperature=payload.temperature,
        max_output_tokens=payload.max_output_tokens,
        session=db,
    )

    return GenerateStructuredResponse(
        data=response.parsed,  # type: ignore[arg-type]
        model=response.model,
        tokens_used=response.usage.total_tokens,
        duration_ms=response.usage.duration_ms,
        estimated_cost_usd=response.usage.estimated_cost_usd,
    )
