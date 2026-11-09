"""Centralized agent execution service coordinating execution and persistence."""

import logging
import uuid
from collections.abc import Sequence

from fastapi import Depends
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.contracts import (
    AgentConfig,
    AgentInput,
    AgentOutput,
)
from app.agents.factory import AgentFactory, get_agent_factory
from app.agents.registry import AgentMetadata, AgentRegistry, get_agent_registry
from app.core.exceptions import AgentNotFoundError, AgentValidationError
from app.db.models.agent_execution import AgentExecution

logger = logging.getLogger("app.services.agent_execution_service")


class AgentExecutionService:
    """Orchestrates single-agent execution, error containment, and audit persistence."""

    def __init__(
        self,
        factory: AgentFactory | None = None,
        registry: AgentRegistry | None = None,
    ) -> None:
        """Initialize service with factory and registry dependencies."""
        self.registry = registry or get_agent_registry()
        self.factory = factory or get_agent_factory(registry=self.registry)

    def list_agents(self) -> list[AgentMetadata]:
        """Return metadata for all registered agents."""
        return self.registry.list_agents()

    def get_agent_details(self, agent_id: str) -> AgentMetadata:
        """Retrieve metadata for a specific agent by ID."""
        return self.registry.get_agent_metadata(agent_id)

    async def _persist_execution_record(
        self,
        session: AsyncSession,
        output: AgentOutput,
        input_data: AgentInput,
    ) -> AgentExecution | None:
        """Persist execution audit record to database."""
        try:
            workflow_uuid = uuid.UUID(input_data.workflow_id) if input_data.workflow_id else None
            task_uuid = uuid.UUID(input_data.task_id) if input_data.task_id else None

            # Build sanitized input summary (exclude huge blobs or sensitive tokens)
            input_summary = {
                "task_description": input_data.task_description[:1000],
                "has_context": bool(input_data.context),
                "context_keys": list(input_data.context.keys()) if input_data.context else [],
                "has_instructions": bool(input_data.instructions),
            }

            # Result is structured dict or str
            result_payload = (
                output.result if isinstance(output.result, dict) else {"text": str(output.result)}
            )

            execution_record = AgentExecution(
                id=uuid.uuid4(),
                workflow_id=workflow_uuid,
                task_id=task_uuid,
                agent_id=output.agent_id,
                agent_name=output.agent_name,
                status=output.status.value,
                input_summary=input_summary,
                output_summary=result_payload,
                reasoning_summary=output.reasoning_summary,
                error_message=output.error,
                duration_ms=output.execution_metadata.get("duration_ms", 0),
                started_at=output.started_at,
                completed_at=output.completed_at,
            )

            session.add(execution_record)
            await session.flush()
            logger.debug(
                "Persisted execution record %s for agent '%s'",
                execution_record.id,
                output.agent_id,
            )
            return execution_record
        except Exception as exc:
            logger.warning("Failed to persist agent execution record: %s", str(exc))
            return None

    async def execute_agent(
        self,
        agent_id: str,
        input_data: AgentInput,
        config: AgentConfig | None = None,
        session: AsyncSession | None = None,
    ) -> AgentOutput:
        """Execute a specialized agent with input validation, telemetry, and persistence.

        Args:
            agent_id: Identifier of registered agent.
            input_data: Standardized AgentInput.
            config: Optional runtime execution configuration.
            session: Optional database session for audit recording.

        Returns:
            Standardized AgentOutput.

        Raises:
            AgentNotFoundError: If agent is not registered.
            AgentValidationError: If input data is malformed.
        """
        if not self.registry.has_agent(agent_id):
            raise AgentNotFoundError(agent_id=agent_id)

        if not input_data.task_description or not input_data.task_description.strip():
            raise AgentValidationError(
                message="Task description cannot be empty or whitespace.",
                agent_id=agent_id,
            )

        agent = self.factory.create_agent(agent_id=agent_id, config=config)

        # Execute agent
        output = await agent.execute(input_data=input_data, session=session)

        # Persist audit log if session is active
        if session is not None:
            await self._persist_execution_record(session, output, input_data)

        return output

    async def get_execution_history(
        self,
        session: AsyncSession,
        agent_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[AgentExecution]:
        """Query agent execution history from the database."""
        query = (
            select(AgentExecution)
            .order_by(desc(AgentExecution.created_at))
            .limit(limit)
            .offset(offset)
        )
        if agent_id:
            query = query.where(AgentExecution.agent_id == agent_id)
        result = await session.execute(query)
        return result.scalars().all()


_default_execution_service: AgentExecutionService | None = None


def get_agent_execution_service(
    factory: AgentFactory = Depends(get_agent_factory),
    registry: AgentRegistry = Depends(get_agent_registry),
) -> AgentExecutionService:
    """FastAPI dependency yielding the AgentExecutionService singleton."""
    global _default_execution_service
    if not isinstance(factory, AgentFactory):
        factory = get_agent_factory()
    if not isinstance(registry, AgentRegistry):
        registry = get_agent_registry()
    if _default_execution_service is None:
        _default_execution_service = AgentExecutionService(factory=factory, registry=registry)
    return _default_execution_service
