"""Reviewer Agent implementation."""

from app.agents.base import BaseAgent
from app.agents.prompts.system_prompts import REVIEWER_SYSTEM_PROMPT
from app.agents.specialized.schemas import ReviewResult


class ReviewerAgent(BaseAgent[ReviewResult]):
    """Reviewer Agent responsible for quality assurance, requirements verification, and critical peer review."""

    agent_id = "reviewer"
    name = "Reviewer Agent"
    role = "Critical Quality Assurance and Deliverable Verification Auditor"
    description = (
        "Audits generated deliverables for logical consistency, completeness, and "
        "adherence to acceptance criteria, issuing structured verdicts (ACCEPT/REVISE/REJECT)."
    )
    system_instruction = REVIEWER_SYSTEM_PROMPT
    output_schema = ReviewResult
