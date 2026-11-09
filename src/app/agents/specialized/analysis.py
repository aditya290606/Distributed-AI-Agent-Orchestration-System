"""Analysis Agent implementation."""

from app.agents.base import BaseAgent
from app.agents.prompts.system_prompts import ANALYSIS_SYSTEM_PROMPT
from app.agents.specialized.schemas import AnalysisResult


class AnalysisAgent(BaseAgent[AnalysisResult]):
    """Analysis Agent responsible for structured data evaluation and comparative trade-off analysis."""

    agent_id = "analyst"
    name = "Analysis Agent"
    role = "Comparative Evaluation and Strategic Trend Analyst"
    description = (
        "Analyzes structured information, evaluates competing technical alternatives, "
        "identifies trends and bottlenecks, and distills evidence-backed conclusions."
    )
    system_instruction = ANALYSIS_SYSTEM_PROMPT
    output_schema = AnalysisResult
