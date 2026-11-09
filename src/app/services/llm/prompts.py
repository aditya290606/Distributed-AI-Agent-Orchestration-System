"""Reusable prompt templates, formatting, and validation."""

import re
from typing import Any


class PromptTemplate:
    """Manages prompt template strings, parameter validation, and formatting."""

    def __init__(
        self,
        name: str,
        template: str,
        system_instruction: str | None = None,
        description: str | None = None,
    ) -> None:
        self.name = name
        self.template = template
        self.system_instruction = system_instruction
        self.description = description
        self.required_variables = self._extract_variables(template)
        if system_instruction:
            self.required_variables.update(self._extract_variables(system_instruction))

    @staticmethod
    def _extract_variables(text: str) -> set[str]:
        """Extract all {var_name} placeholders from a template string."""
        return set(re.findall(r"\{([a-zA-Z0-9_]+)\}", text))

    def format(self, **kwargs: Any) -> tuple[str, str | None]:
        """Format user prompt and system instruction with provided keyword arguments.

        Args:
            **kwargs: Template variable values.

        Returns:
            Tuple of (formatted_user_prompt, formatted_system_instruction).

        Raises:
            ValueError: If required variables are missing.
        """
        missing = self.required_variables - set(kwargs.keys())
        if missing:
            raise ValueError(
                f"Prompt template '{self.name}' is missing required variables: {sorted(missing)}"
            )

        try:
            formatted_prompt = self.template.format(**kwargs)
            formatted_system = (
                self.system_instruction.format(**kwargs) if self.system_instruction else None
            )
            return formatted_prompt, formatted_system
        except KeyError as exc:
            raise ValueError(
                f"Variable formatting failed for template '{self.name}': {exc}"
            ) from exc


# ------------------------------------------------------------------------------
# Pre-defined Reusable Prompt Templates for NEXUS AI
# ------------------------------------------------------------------------------

TASK_DECOMPOSITION_PROMPT = PromptTemplate(
    name="task_decomposition",
    system_instruction=(
        "You are the NEXUS AI Lead Architect. Your task is to analyze the user objective "
        "and decompose it into a clean, dependency-ordered directed acyclic graph (DAG) of subtasks."
    ),
    template=(
        "Objective: {objective}\n\n"
        "Available Agent Types: {available_agents}\n\n"
        "Execution Constraints: {constraints}\n\n"
        "Provide a structured decomposition containing clear, independent subtask units."
    ),
    description="Decomposes a complex objective into structured subtasks.",
)

CODE_REVIEW_PROMPT = PromptTemplate(
    name="code_review",
    system_instruction=(
        "You are an expert Principal Software Engineer and Security Reviewer. "
        "Rigorously analyze the provided code against functional requirements, security boundaries, "
        "clean architecture, and error handling."
    ),
    template=(
        "Language / Framework: {language}\n"
        "Requirements: {requirements}\n\n"
        "Source Code to Review:\n"
        "```\n{code}\n```\n\n"
        "Provide a critique including pass/fail status, quality score (0-100), and specific defects."
    ),
    description="Reviews code deliverables for security and functional correctness.",
)

SUMMARY_PROMPT = PromptTemplate(
    name="summary",
    system_instruction="You are an executive technical summarizer. Synthesize input into concise, actionable points.",
    template="Context to summarize:\n{context}\n\nTarget audience: {audience}",
    description="Summarizes complex analytical context into concise deliverables.",
)
