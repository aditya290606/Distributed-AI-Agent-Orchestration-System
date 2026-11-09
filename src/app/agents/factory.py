"""Agent factory for instantiating specialized agents with injected dependencies."""

import logging
from typing import Any

from fastapi import Depends

from app.agents.base import AgentConfig, BaseAgent
from app.agents.registry import AgentRegistry, get_agent_registry
from app.services.llm.service import LLMService, get_llm_service

logger = logging.getLogger("app.agents.factory")


class AgentFactory:
    """Factory responsible for instantiating specialized agents with dependencies."""

    def __init__(
        self,
        registry: AgentRegistry | None = None,
        llm_service: LLMService | None = None,
    ) -> None:
        """Initialize factory with registry and LLM service dependencies.

        Args:
            registry: Central agent registry. Defaults to singleton registry.
            llm_service: Central LLM service. Defaults to singleton LLM service.
        """
        self.registry = registry or get_agent_registry()
        self.llm_service = llm_service or get_llm_service()

    def create_agent(
        self,
        agent_id: str,
        config: AgentConfig | None = None,
    ) -> BaseAgent[Any]:
        """Instantiate a specialized agent by identifier.

        Args:
            agent_id: Unique identifier of the desired agent.
            config: Optional runtime configuration overrides.

        Returns:
            Configured and initialized BaseAgent instance.

        Raises:
            AgentNotFoundError: If agent_id is not registered.
        """
        agent_cls = self.registry.get_agent_class(agent_id)
        agent_instance = agent_cls(
            llm_service=self.llm_service,
            config=config,
        )
        logger.debug("Instantiated agent '%s' via AgentFactory", agent_id)
        return agent_instance


_default_agent_factory: AgentFactory | None = None


def get_agent_factory(
    registry: AgentRegistry = Depends(get_agent_registry),
    llm_service: LLMService = Depends(get_llm_service),
) -> AgentFactory:
    """Return a shared singleton AgentFactory instance."""
    global _default_agent_factory
    if not isinstance(registry, AgentRegistry):
        registry = get_agent_registry()
    if not isinstance(llm_service, LLMService):
        llm_service = get_llm_service()
    if _default_agent_factory is None:
        _default_agent_factory = AgentFactory(registry=registry, llm_service=llm_service)
    return _default_agent_factory
