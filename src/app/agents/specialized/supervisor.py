"""Supervisor Agent implementation."""

from app.agents.base import BaseAgent
from app.agents.prompts.system_prompts import SUPERVISOR_SYSTEM_PROMPT
from app.agents.specialized.schemas import SupervisorResult


class SupervisorAgent(BaseAgent[SupervisorResult]):
    """Supervisor Agent responsible for classifying tasks and recommending agent delegation strategies."""

    agent_id = "supervisor"
    name = "Supervisor Agent"
    role = "High-Level Objective Classifier and Task Delegation Advisor"
    description = (
        "Parses high-level user objectives, classifies task domain and complexity, "
        "and produces structured delegation recommendations for specialized agents."
    )
    system_instruction = SUPERVISOR_SYSTEM_PROMPT
    output_schema = SupervisorResult
