"""Report Generation Agent implementation."""

from app.agents.base import BaseAgent
from app.agents.prompts.system_prompts import REPORT_SYSTEM_PROMPT
from app.agents.specialized.schemas import ReportResult


class ReportGenerationAgent(BaseAgent[ReportResult]):
    """Report Generation Agent responsible for narrative synthesis and structured executive reporting."""

    agent_id = "reporter"
    name = "Report Generation Agent"
    role = "Executive Synthesis and Narrative Documentation Specialist"
    description = (
        "Synthesizes research, analysis, and multi-agent findings into organized, "
        "professional markdown reports with executive summaries and missing evidence tracking."
    )
    system_instruction = REPORT_SYSTEM_PROMPT
    output_schema = ReportResult
