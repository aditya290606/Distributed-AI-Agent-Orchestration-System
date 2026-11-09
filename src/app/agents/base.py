"""Base agent abstract class and operational framework."""

import json
import logging
from abc import ABC
from datetime import UTC, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.contracts import AgentConfig, AgentExecutionStatus, AgentInput, AgentOutput
from app.core.exceptions import AgentValidationError, AppException
from app.services.llm.service import LLMService

logger = logging.getLogger("app.agents.base")

T = TypeVar("T", bound=BaseModel)


class BaseAgent(ABC, Generic[T]):
    """Abstract base class establishing the contract and lifecycle for all NEXUS AI agents."""

    agent_id: str
    name: str
    role: str
    description: str
    system_instruction: str
    output_schema: type[T] | None = None

    def __init__(
        self,
        llm_service: LLMService,
        config: AgentConfig | None = None,
    ) -> None:
        """Initialize the base agent with injected dependencies.

        Args:
            llm_service: Centralized LLM orchestration service.
            config: Optional runtime configuration parameters.
        """
        self.llm_service = llm_service
        self.config = config or AgentConfig()

    def _build_user_prompt(self, input_data: AgentInput) -> str:
        """Format input description, context, and instructions into a clean prompt string."""
        prompt_sections = [f"### TASK OBJECTIVE:\n{input_data.task_description}"]

        if input_data.context:
            context_str = json.dumps(input_data.context, indent=2, default=str)
            prompt_sections.append(f"### CONTEXT & INPUT DATA:\n{context_str}")

        if input_data.instructions:
            prompt_sections.append(f"### SPECIFIC INSTRUCTIONS:\n{input_data.instructions}")

        if input_data.expected_output:
            prompt_sections.append(f"### EXPECTED DELIVERABLE:\n{input_data.expected_output}")

        return "\n\n".join(prompt_sections)

    def _validate_input(self, input_data: AgentInput) -> None:
        """Validate agent input prior to invocation."""
        if not input_data.task_description or not input_data.task_description.strip():
            raise AgentValidationError(
                message="Task description cannot be empty or whitespace.",
                agent_id=self.agent_id,
            )

    async def execute(
        self,
        input_data: AgentInput,
        session: AsyncSession | None = None,
    ) -> AgentOutput:
        """Execute the agent task asynchronously with validation, timing, and error handling.

        Args:
            input_data: Validated agent input contract.
            session: Optional SQLAlchemy async session for telemetry persistence.

        Returns:
            Standardized AgentOutput with results, concise reasoning summary, and metrics.
        """
        started_at = datetime.now(UTC)
        logger.info(
            "Executing agent '%s' (%s) for task_id=%s workflow_id=%s",
            self.agent_id,
            self.name,
            input_data.task_id,
            input_data.workflow_id,
        )

        try:
            self._validate_input(input_data)
            prompt = self._build_user_prompt(input_data)

            # Execution parameters: agent config takes precedence if set
            temperature = self.config.temperature
            max_output_tokens = self.config.max_output_tokens

            if self.output_schema is not None:
                # Structured generation enforcing Pydantic schema
                response = await self.llm_service.generate_structured(
                    prompt=prompt,
                    response_model=self.output_schema,
                    system_instruction=self.system_instruction,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    session=session,
                )
                parsed_model: T = response.parsed  # type: ignore[assignment]
                result_payload = parsed_model.model_dump()
                # Extract concise reasoning summary from the structured result
                reasoning_summary = getattr(
                    parsed_model,
                    "reasoning_summary",
                    "Execution completed according to structured schema specifications.",
                )
            else:
                # Free-form text generation
                text_response = await self.llm_service.generate_text(
                    prompt=prompt,
                    system_instruction=self.system_instruction,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    session=session,
                )
                result_payload = text_response.content
                reasoning_summary = "Execution completed successfully with natural language output."
                response = text_response

            completed_at = datetime.now(UTC)
            duration_ms = int((completed_at - started_at).total_seconds() * 1000)

            execution_metadata = {
                "model": response.model,
                "tokens_used": response.usage.total_tokens,
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "duration_ms": duration_ms,
                "cost_usd": str(response.usage.estimated_cost_usd)
                if response.usage.estimated_cost_usd is not None
                else None,
            }

            logger.info(
                "Agent '%s' succeeded in %dms (tokens=%d)",
                self.agent_id,
                duration_ms,
                response.usage.total_tokens,
            )

            return AgentOutput(
                agent_id=self.agent_id,
                agent_name=self.name,
                status=AgentExecutionStatus.SUCCESS,
                result=result_payload,
                reasoning_summary=reasoning_summary,
                execution_metadata=execution_metadata,
                error=None,
                started_at=started_at,
                completed_at=completed_at,
            )

        except Exception as exc:
            completed_at = datetime.now(UTC)
            duration_ms = int((completed_at - started_at).total_seconds() * 1000)

            error_msg = exc.message if isinstance(exc, AppException) else str(exc)
            logger.error(
                "Agent '%s' failed after %dms: %s",
                self.agent_id,
                duration_ms,
                error_msg,
            )

            return AgentOutput(
                agent_id=self.agent_id,
                agent_name=self.name,
                status=AgentExecutionStatus.FAILED,
                result={},
                reasoning_summary="Execution failed due to an error encountered during processing.",
                execution_metadata={"duration_ms": duration_ms},
                error=error_msg,
                started_at=started_at,
                completed_at=completed_at,
            )
