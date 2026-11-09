"""Planner Agent implementation."""

from app.agents.base import BaseAgent
from app.agents.prompts.system_prompts import PLANNER_SYSTEM_PROMPT
from app.agents.specialized.schemas import PlannerResult


class PlannerAgent(BaseAgent[PlannerResult]):
    """Planner Agent responsible for decomposing objectives into ordered execution steps."""

    agent_id = "planner"
    name = "Planner Agent"
    role = "Task Decomposition and Execution Sequencing Architect"
    description = (
        "Decomposes complex objectives into discrete, dependency-ordered subtasks "
        "with assigned agent roles and verifiable expected deliverables."
    )
    system_instruction = PLANNER_SYSTEM_PROMPT
    output_schema = PlannerResult
