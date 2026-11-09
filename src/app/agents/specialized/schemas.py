"""Pydantic schemas defining structured outputs for specialized agents."""

from pydantic import BaseModel, Field

# ------------------------------------------------------------------------------
# 1. Supervisor Agent Schemas
# ------------------------------------------------------------------------------


class SupervisorResult(BaseModel):
    """Structured delegation recommendation produced by the Supervisor Agent."""

    task_classification: str = Field(
        ...,
        description="Categorization of the task (e.g. 'research', 'code_generation', 'review')",
    )
    complexity_assessment: str = Field(
        ...,
        description="Assessed complexity level ('low', 'medium', 'high', 'critical')",
    )
    required_capabilities: list[str] = Field(
        default_factory=list,
        description="Required capabilities identified to execute the task successfully",
    )
    recommended_agents: list[str] = Field(
        default_factory=list,
        description="List of specialized agent identifiers recommended for the workflow",
    )
    delegation_strategy: str = Field(
        ...,
        description="High-level sequence or delegation strategy for the selected agents",
    )
    reasoning_summary: str = Field(
        ...,
        description="Concise rationale explaining the classification and agent selection decisions",
    )


# ------------------------------------------------------------------------------
# 2. Planner Agent Schemas
# ------------------------------------------------------------------------------


class TaskPlanStep(BaseModel):
    """Discrete step within a decomposed task execution plan."""

    step_number: int = Field(..., ge=1, description="Sequential index of the step")
    task_name: str = Field(..., description="Short descriptive name of the subtask")
    assigned_agent: str = Field(
        ...,
        description="Target specialized agent identifier (e.g. 'researcher', 'coder', 'analyst')",
    )
    dependencies: list[int] = Field(
        default_factory=list,
        description="List of step numbers that must finish before this step begins",
    )
    expected_output: str = Field(
        ...,
        description="Specific deliverable or artifact produced by this step",
    )


class PlannerResult(BaseModel):
    """Structured execution plan decomposed by the Planner Agent."""

    objective: str = Field(..., description="Normalized understanding of the overall objective")
    total_steps: int = Field(..., ge=1, description="Total count of discrete execution steps")
    execution_order: list[int] = Field(
        default_factory=list,
        description="Recommended topological sequence of step numbers",
    )
    steps: list[TaskPlanStep] = Field(
        default_factory=list,
        description="Detailed list of planned execution steps",
    )
    estimated_effort: str = Field(
        ...,
        description="Overall effort/resource evaluation (e.g. 'low', 'moderate', 'high')",
    )
    reasoning_summary: str = Field(
        ...,
        description="Concise rationale explaining the decomposition and dependency choices",
    )


# ------------------------------------------------------------------------------
# 3. Research Agent Schemas
# ------------------------------------------------------------------------------


class ResearchFinding(BaseModel):
    """Specific finding or factual synthesis item."""

    topic: str = Field(..., description="Specific subtopic or question addressed")
    key_information: str = Field(..., description="Synthesized facts and knowledge")
    confidence_level: str = Field(
        ...,
        description="Assessment of confidence ('high', 'medium', 'low')",
    )
    requires_external_verification: bool = Field(
        default=False,
        description="Flag indicating if the fact requires live external tool verification",
    )


class ResearchResult(BaseModel):
    """Structured research synthesis produced by the Research Agent."""

    research_question: str = Field(..., description="Primary topic or question investigated")
    executive_summary: str = Field(..., description="High-level synthesis of findings")
    findings: list[ResearchFinding] = Field(
        default_factory=list,
        description="List of categorized research findings",
    )
    assumptions: list[str] = Field(
        default_factory=list,
        description="Key assumptions made during synthesis based on provided context",
    )
    limitations: list[str] = Field(
        default_factory=list,
        description="Known constraints or scope limitations of the knowledge base",
    )
    information_gaps: list[str] = Field(
        default_factory=list,
        description="Areas where additional information is needed for completeness",
    )
    reasoning_summary: str = Field(
        ...,
        description="Concise rationale summarizing research methodology and key deductions",
    )


# ------------------------------------------------------------------------------
# 4. Analysis Agent Schemas
# ------------------------------------------------------------------------------


class AlternativeComparison(BaseModel):
    """Comparison evaluation between different approaches or entities."""

    alternative_name: str = Field(..., description="Name of the evaluated option")
    strengths: list[str] = Field(default_factory=list, description="Key advantages")
    weaknesses: list[str] = Field(default_factory=list, description="Key disadvantages")
    score_or_ranking: str = Field(
        ...,
        description="Qualitative or comparative ranking (e.g. 'Rank 1 / Preferred', 'Viable')",
    )


class AnalysisResult(BaseModel):
    """Analytical evaluation and trend assessment produced by the Analysis Agent."""

    analysis_focus: str = Field(..., description="Core subject or hypothesis evaluated")
    identified_trends: list[str] = Field(
        default_factory=list,
        description="Patterns, correlations, or trends identified in data",
    )
    comparisons: list[AlternativeComparison] = Field(
        default_factory=list,
        description="Comparative analysis of competing solutions or designs",
    )
    key_insights: list[str] = Field(
        default_factory=list,
        description="Critical discoveries or takeaways derived from analysis",
    )
    analytical_conclusion: str = Field(
        ...,
        description="Definitive conclusion and strategic takeaway",
    )
    reasoning_summary: str = Field(
        ...,
        description="Concise summary of analytical deductions and trade-off evaluations",
    )


# ------------------------------------------------------------------------------
# 5. Coding Agent Schemas
# ------------------------------------------------------------------------------


class CodeBlock(BaseModel):
    """Individual code implementation deliverable."""

    filename_or_module: str = Field(..., description="Target file path or module name")
    language: str = Field(..., description="Programming language (e.g. 'python', 'sql')")
    code: str = Field(..., description="Exact code implementation")
    explanation: str = Field(..., description="Summary of how this block solves the requirement")


class CodingResult(BaseModel):
    """Technical architecture and code deliverables produced by the Coding Agent."""

    technical_solution: str = Field(..., description="Overview of the technical design")
    architecture_notes: str = Field(
        ...,
        description="Architectural considerations, dependencies, and integration points",
    )
    code_blocks: list[CodeBlock] = Field(
        default_factory=list,
        description="List of generated code artifacts",
    )
    edge_cases: list[str] = Field(
        default_factory=list,
        description="Edge cases considered and defended against in the implementation",
    )
    testing_recommendations: list[str] = Field(
        default_factory=list,
        description="Recommended unit, integration, and regression test strategies",
    )
    reasoning_summary: str = Field(
        ...,
        description="Concise rationale explaining algorithmic choices and architectural trade-offs",
    )


# ------------------------------------------------------------------------------
# 6. Reviewer Agent Schemas
# ------------------------------------------------------------------------------


class ReviewFinding(BaseModel):
    """Specific observation or defect identified during review."""

    category: str = Field(
        ...,
        description="Evaluation category ('logical_consistency', 'completeness', 'correctness', 'style')",
    )
    severity: str = Field(
        ...,
        description="Impact severity ('critical', 'major', 'minor', 'info')",
    )
    description: str = Field(..., description="Clear explanation of the observation or defect")
    recommendation: str = Field(..., description="Actionable change recommended to resolve it")


class ReviewResult(BaseModel):
    """Structured quality assessment and verdict produced by the Reviewer Agent."""

    target_artifact_name: str = Field(..., description="Name or identifier of artifact reviewed")
    meets_requirements: bool = Field(
        ...,
        description="Whether the reviewed artifact satisfies all specified requirements",
    )
    verdict: str = Field(
        ...,
        description="Formal review disposition ('ACCEPT', 'REVISE', 'REJECT')",
    )
    findings: list[ReviewFinding] = Field(
        default_factory=list,
        description="Detailed list of review findings",
    )
    improvement_recommendations: list[str] = Field(
        default_factory=list,
        description="Key action items required prior to final acceptance",
    )
    reasoning_summary: str = Field(
        ...,
        description="Concise justification of the verdict based on evidence and criteria",
    )


# ------------------------------------------------------------------------------
# 7. Report Generation Agent Schemas
# ------------------------------------------------------------------------------


class ReportSection(BaseModel):
    """Section of a structured synthesis report."""

    title: str = Field(..., description="Section heading")
    content: str = Field(..., description="Detailed body text formatted in clean markdown")
    key_takeaways: list[str] = Field(
        default_factory=list,
        description="Bullet points highlighting core takeaways",
    )


class ReportResult(BaseModel):
    """Comprehensive executive report produced by the Report Generation Agent."""

    report_title: str = Field(..., description="Title of the generated report")
    executive_summary: str = Field(..., description="Concise executive summary for stakeholders")
    sections: list[ReportSection] = Field(
        default_factory=list,
        description="Organized topical sections of the report",
    )
    conclusions: list[str] = Field(
        default_factory=list,
        description="Final conclusions synthesized from provided facts",
    )
    missing_evidence: list[str] = Field(
        default_factory=list,
        description="Explicit enumeration of missing or incomplete evidence noted in source",
    )
    reasoning_summary: str = Field(
        ...,
        description="Concise rationale explaining report organization and evidence synthesis",
    )
