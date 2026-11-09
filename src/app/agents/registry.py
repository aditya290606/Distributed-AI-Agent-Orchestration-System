"""Centralized agent registry for registering and discovering specialized agents."""

import logging
from typing import Any

from pydantic import BaseModel, Field

from app.agents.base import BaseAgent
from app.core.exceptions import AgentNotFoundError, AgentRegistrationError

logger = logging.getLogger("app.agents.registry")


class AgentMetadata(BaseModel):
    """Metadata describing a registered specialized agent."""

    agent_id: str = Field(..., description="Unique machine-readable agent identifier")
    name: str = Field(..., description="Human-readable display name")
    role: str = Field(..., description="Primary functional role")
    description: str = Field(..., description="Detailed description of responsibilities")
    output_schema_name: str | None = Field(
        default=None,
        description="Name of the enforced Pydantic output schema, if structured",
    )
    capabilities: list[str] = Field(
        default_factory=list,
        description="List of core capabilities provided by this agent",
    )


class AgentRegistry:
    """Thread-safe registry managing specialized agent classes and their metadata."""

    def __init__(self) -> None:
        self._registry: dict[str, type[BaseAgent[Any]]] = {}

    def register(self, agent_class: type[BaseAgent[Any]]) -> None:
        """Register a specialized agent class in the registry.

        Args:
            agent_class: The subclass of BaseAgent to register.

        Raises:
            AgentRegistrationError: If class does not inherit BaseAgent or is already registered.
        """
        if not issubclass(agent_class, BaseAgent):
            raise AgentRegistrationError(
                message=f"Class '{agent_class.__name__}' must inherit from BaseAgent.",
            )

        agent_id = getattr(agent_class, "agent_id", None)
        if not agent_id or not isinstance(agent_id, str):
            raise AgentRegistrationError(
                message=f"Agent class '{agent_class.__name__}' must declare a valid 'agent_id' string attribute.",
            )

        if agent_id in self._registry:
            raise AgentRegistrationError(
                message=f"Agent with identifier '{agent_id}' is already registered.",
                agent_id=agent_id,
            )

        self._registry[agent_id] = agent_class
        logger.info("Registered agent '%s' (%s)", agent_id, agent_class.name)

    def get_agent_class(self, agent_id: str) -> type[BaseAgent[Any]]:
        """Retrieve an agent class by its unique identifier.

        Args:
            agent_id: Unique agent identifier.

        Returns:
            The registered BaseAgent subclass.

        Raises:
            AgentNotFoundError: If the agent_id is not in the registry.
        """
        if agent_id not in self._registry:
            raise AgentNotFoundError(agent_id=agent_id)
        return self._registry[agent_id]

    def has_agent(self, agent_id: str) -> bool:
        """Check whether an agent is registered."""
        return agent_id in self._registry

    def list_agents(self) -> list[AgentMetadata]:
        """Return a list of metadata for all registered agents."""
        metadata_list: list[AgentMetadata] = []
        for agent_id, cls in self._registry.items():
            schema_name = cls.output_schema.__name__ if cls.output_schema else None
            metadata = AgentMetadata(
                agent_id=agent_id,
                name=cls.name,
                role=cls.role,
                description=cls.description,
                output_schema_name=schema_name,
                capabilities=[cls.role],
            )
            metadata_list.append(metadata)
        return metadata_list

    def get_agent_metadata(self, agent_id: str) -> AgentMetadata:
        """Retrieve metadata for a specific agent.

        Args:
            agent_id: Unique agent identifier.

        Returns:
            AgentMetadata for the requested agent.
        """
        cls = self.get_agent_class(agent_id)
        schema_name = cls.output_schema.__name__ if cls.output_schema else None
        return AgentMetadata(
            agent_id=agent_id,
            name=cls.name,
            role=cls.role,
            description=cls.description,
            output_schema_name=schema_name,
            capabilities=[cls.role],
        )


# Global singleton registry instance
_default_agent_registry: AgentRegistry | None = None


def get_agent_registry() -> AgentRegistry:
    """Return the shared AgentRegistry singleton populated with standard agents."""
    global _default_agent_registry
    if _default_agent_registry is None:
        registry = AgentRegistry()

        # Import and register the 7 standard specialized agents safely
        from app.agents.specialized.analysis import AnalysisAgent
        from app.agents.specialized.coding import CodingAgent
        from app.agents.specialized.planner import PlannerAgent
        from app.agents.specialized.report import ReportGenerationAgent
        from app.agents.specialized.research import ResearchAgent
        from app.agents.specialized.reviewer import ReviewerAgent
        from app.agents.specialized.supervisor import SupervisorAgent

        registry.register(SupervisorAgent)
        registry.register(PlannerAgent)
        registry.register(ResearchAgent)
        registry.register(AnalysisAgent)
        registry.register(CodingAgent)
        registry.register(ReviewerAgent)
        registry.register(ReportGenerationAgent)

        _default_agent_registry = registry
    return _default_agent_registry
