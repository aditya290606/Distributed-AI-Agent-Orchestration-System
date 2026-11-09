"""Agent capability matching subsystem for aligning planned tasks to registered specialized agents."""

import logging

from pydantic import BaseModel, Field

from app.agents.registry import AgentRegistry, get_agent_registry

logger = logging.getLogger("app.planning.matcher")


class AgentCapabilityProfile(BaseModel):
    """Profile specifying the functional capabilities and supported task types of an agent."""

    agent_id: str
    name: str
    role: str
    description: str
    supported_task_types: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)


class CapabilityMatchResult(BaseModel):
    """Result of matching a planned task against registered agent capabilities."""

    matched_agent_id: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    selection_reason: str
    matched_capabilities: list[str] = Field(default_factory=list)
    missing_capabilities: list[str] = Field(default_factory=list)
    alternative_agents: list[str] = Field(default_factory=list)


# Canonical profiles for the 7 specialized agents
STANDARD_CAPABILITY_PROFILES: dict[str, dict[str, list[str]]] = {
    "supervisor": {
        "task_types": ["orchestration", "supervision", "classification", "routing"],
        "capabilities": [
            "task_classification",
            "agent_selection",
            "workflow_routing",
            "complexity_assessment",
        ],
    },
    "planner": {
        "task_types": ["planning", "decomposition", "sequencing"],
        "capabilities": [
            "task_decomposition",
            "dependency_analysis",
            "execution_sequencing",
            "plan_generation",
        ],
    },
    "researcher": {
        "task_types": ["research", "retrieval", "fact_checking", "investigation"],
        "capabilities": [
            "information_synthesis",
            "knowledge_retrieval",
            "assumption_identification",
            "fact_verification",
        ],
    },
    "analyst": {
        "task_types": ["analysis", "tradeoff_evaluation", "comparison", "data_evaluation"],
        "capabilities": [
            "comparative_analysis",
            "tradeoff_evaluation",
            "trend_detection",
            "feasibility_assessment",
        ],
    },
    "coder": {
        "task_types": [
            "coding",
            "implementation",
            "development",
            "architecture",
            "refactoring",
            "scripting",
        ],
        "capabilities": [
            "code_generation",
            "technical_architecture",
            "edge_case_detection",
            "api_design",
        ],
    },
    "reviewer": {
        "task_types": ["review", "qa", "verification", "audit", "testing"],
        "capabilities": [
            "code_audit",
            "quality_assurance",
            "defect_detection",
            "criteria_verification",
        ],
    },
    "reporter": {
        "task_types": ["report", "reporting", "documentation", "summary", "synthesis"],
        "capabilities": [
            "executive_reporting",
            "markdown_formatting",
            "actionable_synthesis",
            "summary_generation",
        ],
    },
}


class AgentCapabilityMatcher:
    """Matches task requirements against registered agent capabilities with explainability."""

    def __init__(self, registry: AgentRegistry | None = None) -> None:
        self.registry = registry or get_agent_registry()

    def get_agent_profile(self, agent_id: str) -> AgentCapabilityProfile | None:
        """Retrieve capability profile for a registered agent."""
        if not self.registry.has_agent(agent_id):
            return None

        metadata = self.registry.get_agent_metadata(agent_id)
        standard = STANDARD_CAPABILITY_PROFILES.get(agent_id, {})
        return AgentCapabilityProfile(
            agent_id=agent_id,
            name=metadata.name,
            role=metadata.role,
            description=metadata.description,
            supported_task_types=standard.get("task_types", [agent_id]),
            capabilities=standard.get("capabilities", metadata.capabilities),
        )

    def list_all_profiles(self) -> list[AgentCapabilityProfile]:
        """List profiles for all registered agents."""
        profiles: list[AgentCapabilityProfile] = []
        for meta in self.registry.list_agents():
            profile = self.get_agent_profile(meta.agent_id)
            if profile:
                profiles.append(profile)
        return profiles

    def match_task(
        self,
        task_type: str,
        required_capabilities: list[str] | None = None,
        preferred_agent_id: str | None = None,
    ) -> CapabilityMatchResult:
        """Determine the optimal registered agent for a planned task.

        Args:
            task_type: Normalized classification of work (e.g. 'coding', 'research').
            required_capabilities: Specific capabilities needed.
            preferred_agent_id: Optional agent suggested by the planner.

        Returns:
            CapabilityMatchResult detailing selected agent, confidence, and rationale.
        """
        required_caps = [c.lower().strip() for c in (required_capabilities or [])]
        task_type_norm = task_type.lower().strip()

        # 1. Deterministic direct check: if preferred_agent_id is registered and valid
        if preferred_agent_id and self.registry.has_agent(preferred_agent_id):
            profile = self.get_agent_profile(preferred_agent_id)
            if profile:
                matched_caps = [c for c in required_caps if c in profile.capabilities]
                missing_caps = [c for c in required_caps if c not in profile.capabilities]

                # Check if task type matches or is generic
                type_matches = (
                    task_type_norm in profile.supported_task_types
                    or task_type_norm == preferred_agent_id
                )
                confidence = (
                    1.0 if type_matches and not missing_caps else (0.85 if type_matches else 0.7)
                )

                return CapabilityMatchResult(
                    matched_agent_id=preferred_agent_id,
                    confidence=confidence,
                    selection_reason=(
                        f"Directly matched preferred agent '{preferred_agent_id}' ({profile.name}) "
                        f"which supports task type '{task_type_norm}'."
                    ),
                    matched_capabilities=matched_caps,
                    missing_capabilities=missing_caps,
                    alternative_agents=self._find_alternatives(
                        task_type_norm, exclude_agent=preferred_agent_id
                    ),
                )

        # 2. Capability & Task Type Search across all registered agents
        scored_candidates: list[tuple[str, float, list[str], list[str], str]] = []

        for profile in self.list_all_profiles():
            score = 0.0
            matched_caps = [c for c in required_caps if c in profile.capabilities]
            missing_caps = [c for c in required_caps if c not in profile.capabilities]

            # Task type match scoring
            if task_type_norm in profile.supported_task_types or task_type_norm == profile.agent_id:
                score += 0.5
            elif any(task_type_norm in t for t in profile.supported_task_types):
                score += 0.3

            # Capability match scoring
            if required_caps:
                caps_ratio = len(matched_caps) / len(required_caps)
                score += 0.5 * caps_ratio
            elif score > 0:
                score += 0.3

            if score > 0.1:
                reason = (
                    f"Agent '{profile.agent_id}' matches task type '{task_type_norm}' "
                    f"with {len(matched_caps)}/{len(required_caps)} capabilities matched."
                )
                scored_candidates.append(
                    (profile.agent_id, min(score, 1.0), matched_caps, missing_caps, reason)
                )

        scored_candidates.sort(key=lambda x: x[1], reverse=True)

        if scored_candidates:
            best_agent_id, confidence, matched_caps, missing_caps, reason = scored_candidates[0]
            alternatives = [c[0] for c in scored_candidates[1:]]
            return CapabilityMatchResult(
                matched_agent_id=best_agent_id,
                confidence=confidence,
                selection_reason=reason,
                matched_capabilities=matched_caps,
                missing_capabilities=missing_caps,
                alternative_agents=alternatives,
            )

        # 3. Fallback: Default to 'researcher' for knowledge or 'supervisor'
        fallback_agent = "researcher" if self.registry.has_agent("researcher") else "supervisor"
        logger.warning(
            "No registered agent matched task_type='%s'. Falling back to '%s'.",
            task_type_norm,
            fallback_agent,
        )
        return CapabilityMatchResult(
            matched_agent_id=fallback_agent,
            confidence=0.3,
            selection_reason=f"No direct match for task type '{task_type_norm}'. Assigned generalist fallback agent '{fallback_agent}'.",
            matched_capabilities=[],
            missing_capabilities=required_caps,
            alternative_agents=[],
        )

    def _find_alternatives(self, task_type_norm: str, exclude_agent: str) -> list[str]:
        """Find alternative agents capable of handling the task type."""
        alternatives: list[str] = []
        for profile in self.list_all_profiles():
            if profile.agent_id != exclude_agent and (
                task_type_norm in profile.supported_task_types or task_type_norm == profile.agent_id
            ):
                alternatives.append(profile.agent_id)
        return alternatives
