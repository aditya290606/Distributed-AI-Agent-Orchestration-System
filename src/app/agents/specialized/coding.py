"""Coding Agent implementation."""

from app.agents.base import BaseAgent
from app.agents.prompts.system_prompts import CODING_SYSTEM_PROMPT
from app.agents.specialized.schemas import CodingResult


class CodingAgent(BaseAgent[CodingResult]):
    """Coding Agent responsible for technical design, code generation, and edge case defense."""

    agent_id = "coder"
    name = "Coding Agent"
    role = "Software Implementation and Technical Architecture Engineer"
    description = (
        "Translates requirements into clean, idiomatic code implementations, provides "
        "architectural notes, analyzes edge cases, and recommends testing strategies. "
        "Operates under strict security boundaries with no arbitrary execution privileges."
    )
    system_instruction = CODING_SYSTEM_PROMPT
    output_schema = CodingResult
