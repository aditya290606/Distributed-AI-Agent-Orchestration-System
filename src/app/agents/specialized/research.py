"""Research Agent implementation."""

from app.agents.base import BaseAgent
from app.agents.prompts.system_prompts import RESEARCHER_SYSTEM_PROMPT
from app.agents.specialized.schemas import ResearchResult


class ResearchAgent(BaseAgent[ResearchResult]):
    """Research Agent responsible for factual synthesis and identifying information gaps."""

    agent_id = "researcher"
    name = "Research Agent"
    role = "Factual Knowledge Synthesizer and Information Gap Analyzer"
    description = (
        "Investigates research topics, synthesizes factual knowledge, documents assumptions "
        "and scope limitations, and flags items requiring external verification."
    )
    system_instruction = RESEARCHER_SYSTEM_PROMPT
    output_schema = ResearchResult
