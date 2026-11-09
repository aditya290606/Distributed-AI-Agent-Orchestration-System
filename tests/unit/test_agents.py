"""Unit tests for specialized agent framework, contracts, registry, factory, and tools."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentConfig
from app.agents.contracts import AgentExecutionStatus, AgentInput, AgentOutput
from app.agents.factory import AgentFactory
from app.agents.prompts.system_prompts import (
    ANALYSIS_SYSTEM_PROMPT,
    CODING_SYSTEM_PROMPT,
    PLANNER_SYSTEM_PROMPT,
    REPORT_SYSTEM_PROMPT,
    RESEARCHER_SYSTEM_PROMPT,
    REVIEWER_SYSTEM_PROMPT,
    SUPERVISOR_SYSTEM_PROMPT,
)
from app.agents.registry import AgentRegistry, get_agent_registry
from app.agents.specialized import (
    AnalysisAgent,
    AnalysisResult,
    CodingAgent,
    CodingResult,
    PlannerAgent,
    PlannerResult,
    ReportGenerationAgent,
    ReportResult,
    ResearchAgent,
    ResearchResult,
    ReviewerAgent,
    ReviewResult,
    SupervisorAgent,
    SupervisorResult,
)
from app.agents.tools.registry import (
    ControlledToolRegistry,
    ToolDefinition,
    ToolPermission,
)
from app.core.exceptions import (
    AgentNotFoundError,
    AgentRegistrationError,
    LLMAuthenticationError,
    ToolRegistrationError,
)
from app.services.agent_execution_service import (
    AgentExecutionService,
    get_agent_execution_service,
)
from app.services.llm.base import LLMResponse, LLMUsage
from app.services.llm.service import LLMService

# ------------------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------------------


@pytest.fixture
def mock_llm_service() -> MagicMock:
    """Fixture providing a mock LLMService."""
    service = MagicMock(spec=LLMService)
    return service


@pytest.fixture
def sample_usage() -> LLMUsage:
    """Fixture providing sample LLM token usage."""
    return LLMUsage(
        prompt_tokens=50,
        completion_tokens=100,
        total_tokens=150,
        duration_ms=350,
        estimated_cost_usd=Decimal("0.000030"),
        model_name="gemini-2.5-flash",
    )


# ------------------------------------------------------------------------------
# 1. Agent Contracts Tests
# ------------------------------------------------------------------------------


def test_agent_input_validation():
    """Verify AgentInput accepts valid data and rejects empty task description."""
    valid_input = AgentInput(
        task_description="Analyze system throughput",
        context={"qps": 5000},
        instructions="Focus on bottlenecks",
        expected_output="Summary report",
    )
    assert valid_input.task_description == "Analyze system throughput"
    assert valid_input.context["qps"] == 5000
    assert valid_input.metadata == {}

    with pytest.raises(ValidationError):
        AgentInput(task_description="")


def test_agent_output_contracts():
    """Verify AgentOutput data integrity and serialization."""
    now = datetime.now(UTC)
    output = AgentOutput(
        agent_id="analyst",
        agent_name="Analysis Agent",
        status=AgentExecutionStatus.SUCCESS,
        result={"summary": "Throughput optimal"},
        reasoning_summary="Deductions based on QPS metrics.",
        execution_metadata={"tokens_used": 150},
        error=None,
        started_at=now,
        completed_at=now,
    )
    assert output.status == AgentExecutionStatus.SUCCESS
    assert output.result["summary"] == "Throughput optimal"
    assert "Deductions based on QPS" in output.reasoning_summary


# ------------------------------------------------------------------------------
# 2. BaseAgent Execution Lifecycle & Error Containment
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_base_agent_successful_execution(mock_llm_service: MagicMock, sample_usage: LLMUsage):
    """Verify BaseAgent lifecycle, structured response extraction, and metadata capture."""
    mock_result = SupervisorResult(
        task_classification="code_generation",
        complexity_assessment="medium",
        required_capabilities=["code_design", "testing"],
        recommended_agents=["coder", "reviewer"],
        delegation_strategy="Coder generates implementation, reviewer validates.",
        reasoning_summary="Classification determined from programming prompt requirements.",
    )

    mock_llm_service.generate_structured = AsyncMock(
        return_value=LLMResponse(
            content="{}",
            parsed=mock_result,
            usage=sample_usage,
            model="gemini-2.5-flash",
        )
    )

    agent = SupervisorAgent(llm_service=mock_llm_service)
    agent_input = AgentInput(
        task_description="Build a REST API endpoint for user authentication.",
    )

    output = await agent.execute(agent_input)

    assert output.status == AgentExecutionStatus.SUCCESS
    assert output.agent_id == "supervisor"
    assert output.result["task_classification"] == "code_generation"
    assert output.result["recommended_agents"] == ["coder", "reviewer"]
    assert output.reasoning_summary == mock_result.reasoning_summary
    assert output.execution_metadata["tokens_used"] == 150
    assert output.error is None
    assert output.started_at <= output.completed_at


@pytest.mark.asyncio
async def test_base_agent_empty_task_description_fails(mock_llm_service: MagicMock):
    """Verify BaseAgent rejects empty whitespace task description with FAILED status."""
    agent = SupervisorAgent(llm_service=mock_llm_service)
    # Bypass Pydantic validation by constructing with whitespace to test BaseAgent._validate_input
    agent_input = AgentInput(task_description="   ")

    output = await agent.execute(agent_input)
    assert output.status == AgentExecutionStatus.FAILED
    assert "Task description cannot be empty" in (output.error or "")


@pytest.mark.asyncio
async def test_base_agent_llm_failure_containment(mock_llm_service: MagicMock):
    """Verify BaseAgent catches LLM failures gracefully and returns FAILED status without raising."""
    mock_llm_service.generate_structured = AsyncMock(
        side_effect=LLMAuthenticationError("API key expired.")
    )

    agent = PlannerAgent(llm_service=mock_llm_service)
    agent_input = AgentInput(task_description="Create a test plan.")

    output = await agent.execute(agent_input)

    assert output.status == AgentExecutionStatus.FAILED
    assert output.agent_id == "planner"
    assert "API key expired" in (output.error or "")
    assert output.result == {}


# ------------------------------------------------------------------------------
# 3. Specialized Agents Tests (All 7 Agents)
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planner_agent_execution(mock_llm_service: MagicMock, sample_usage: LLMUsage):
    """Verify PlannerAgent execution and structured task plan output."""
    mock_plan = PlannerResult(
        objective="Deploy microservices",
        total_steps=2,
        execution_order=[1, 2],
        steps=[
            {
                "step_number": 1,
                "task_name": "Setup DB",
                "assigned_agent": "coder",
                "dependencies": [],
                "expected_output": "Schema migrations",
            },
            {
                "step_number": 2,
                "task_name": "Review DB",
                "assigned_agent": "reviewer",
                "dependencies": [1],
                "expected_output": "Review report",
            },
        ],
        estimated_effort="moderate",
        reasoning_summary="Two sequential steps with dependencies.",
    )

    mock_llm_service.generate_structured = AsyncMock(
        return_value=LLMResponse(
            content="{}",
            parsed=mock_plan,
            usage=sample_usage,
            model="gemini-2.5-flash",
        )
    )

    planner = PlannerAgent(llm_service=mock_llm_service)
    output = await planner.execute(AgentInput(task_description="Deconstruct deployment."))

    assert output.status == AgentExecutionStatus.SUCCESS
    assert output.result["total_steps"] == 2
    assert len(output.result["steps"]) == 2


@pytest.mark.asyncio
async def test_research_agent_execution(mock_llm_service: MagicMock, sample_usage: LLMUsage):
    """Verify ResearchAgent execution and research result structure."""
    mock_research = ResearchResult(
        research_question="Vector DB comparison",
        executive_summary="Analyzed pgvector and Milvus.",
        findings=[
            {
                "topic": "pgvector",
                "key_information": "PostgreSQL extension for embeddings",
                "confidence_level": "high",
                "requires_external_verification": False,
            }
        ],
        assumptions=["Relational schema already in PG"],
        limitations=["Knowledge cutoff 2026"],
        information_gaps=["Benchmarking at 10M scale"],
        reasoning_summary="Evaluated based on relational co-location.",
    )

    mock_llm_service.generate_structured = AsyncMock(
        return_value=LLMResponse(
            content="{}",
            parsed=mock_research,
            usage=sample_usage,
            model="gemini-2.5-flash",
        )
    )

    agent = ResearchAgent(llm_service=mock_llm_service)
    output = await agent.execute(AgentInput(task_description="Research vector storage options."))

    assert output.status == AgentExecutionStatus.SUCCESS
    assert output.result["research_question"] == "Vector DB comparison"
    assert len(output.result["findings"]) == 1


@pytest.mark.asyncio
async def test_analysis_agent_execution(mock_llm_service: MagicMock, sample_usage: LLMUsage):
    """Verify AnalysisAgent comparative evaluations."""
    mock_analysis = AnalysisResult(
        analysis_focus="Asyncio vs Threading",
        identified_trends=["IO-bound workloads favor coroutines"],
        comparisons=[
            {
                "alternative_name": "Asyncio",
                "strengths": ["Low memory footprint"],
                "weaknesses": ["Blocking calls freeze loop"],
                "score_or_ranking": "Preferred",
            }
        ],
        key_insights=["Asyncio scales to 10k connections with minimal overhead"],
        analytical_conclusion="Asyncio is optimal for network IO.",
        reasoning_summary="Compared concurrency models across memory and throughput.",
    )

    mock_llm_service.generate_structured = AsyncMock(
        return_value=LLMResponse(
            content="{}",
            parsed=mock_analysis,
            usage=sample_usage,
            model="gemini-2.5-flash",
        )
    )

    agent = AnalysisAgent(llm_service=mock_llm_service)
    output = await agent.execute(AgentInput(task_description="Analyze concurrency options."))

    assert output.status == AgentExecutionStatus.SUCCESS
    assert output.result["analytical_conclusion"] == "Asyncio is optimal for network IO."


@pytest.mark.asyncio
async def test_coding_agent_execution(mock_llm_service: MagicMock, sample_usage: LLMUsage):
    """Verify CodingAgent produces code blocks and edge cases without executing them."""
    mock_coding = CodingResult(
        technical_solution="FastAPI health check route",
        architecture_notes="Standard APIRouter implementation",
        code_blocks=[
            {
                "filename_or_module": "src/app/api/health.py",
                "language": "python",
                "code": "def check(): return {'status': 'ok'}",
                "explanation": "Returns health dictionary",
            }
        ],
        edge_cases=["Database connection failure"],
        testing_recommendations=["Add TestClient test for 200 OK"],
        reasoning_summary="Implemented minimal stateless route.",
    )

    mock_llm_service.generate_structured = AsyncMock(
        return_value=LLMResponse(
            content="{}",
            parsed=mock_coding,
            usage=sample_usage,
            model="gemini-2.5-flash",
        )
    )

    agent = CodingAgent(llm_service=mock_llm_service)
    output = await agent.execute(AgentInput(task_description="Write health route."))

    assert output.status == AgentExecutionStatus.SUCCESS
    assert len(output.result["code_blocks"]) == 1
    assert output.result["code_blocks"][0]["language"] == "python"


@pytest.mark.asyncio
async def test_reviewer_agent_execution(mock_llm_service: MagicMock, sample_usage: LLMUsage):
    """Verify ReviewerAgent produces structured verdict and recommendations."""
    mock_review = ReviewResult(
        target_artifact_name="health_check_pr",
        meets_requirements=True,
        verdict="ACCEPT",
        findings=[
            {
                "category": "completeness",
                "severity": "info",
                "description": "Comprehensive test coverage verified",
                "recommendation": "None",
            }
        ],
        improvement_recommendations=[],
        reasoning_summary="Artifact adheres to all specifications.",
    )

    mock_llm_service.generate_structured = AsyncMock(
        return_value=LLMResponse(
            content="{}",
            parsed=mock_review,
            usage=sample_usage,
            model="gemini-2.5-flash",
        )
    )

    agent = ReviewerAgent(llm_service=mock_llm_service)
    output = await agent.execute(AgentInput(task_description="Review pull request."))

    assert output.status == AgentExecutionStatus.SUCCESS
    assert output.result["verdict"] == "ACCEPT"
    assert output.result["meets_requirements"] is True


@pytest.mark.asyncio
async def test_report_agent_execution(mock_llm_service: MagicMock, sample_usage: LLMUsage):
    """Verify ReportGenerationAgent produces executive reports."""
    mock_report = ReportResult(
        report_title="Milestone 3 Audit",
        executive_summary="Specialized agents established successfully.",
        sections=[
            {
                "title": "Architecture Overview",
                "content": "BaseAgent contract and 7 specialized agents.",
                "key_takeaways": ["Modular design", "Thread-safe registry"],
            }
        ],
        conclusions=["System is production ready for orchestration."],
        missing_evidence=[],
        reasoning_summary="Synthesized milestone implementation deliverables.",
    )

    mock_llm_service.generate_structured = AsyncMock(
        return_value=LLMResponse(
            content="{}",
            parsed=mock_report,
            usage=sample_usage,
            model="gemini-2.5-flash",
        )
    )

    agent = ReportGenerationAgent(llm_service=mock_llm_service)
    output = await agent.execute(AgentInput(task_description="Generate milestone report."))

    assert output.status == AgentExecutionStatus.SUCCESS
    assert output.result["report_title"] == "Milestone 3 Audit"
    assert len(output.result["sections"]) == 1


# ------------------------------------------------------------------------------
# 4. Agent Prompts Verification
# ------------------------------------------------------------------------------


def test_agent_system_prompts_completeness():
    """Verify all 7 system prompts are non-empty, distinct, and contain operational boundaries."""
    prompts = [
        SUPERVISOR_SYSTEM_PROMPT,
        PLANNER_SYSTEM_PROMPT,
        RESEARCHER_SYSTEM_PROMPT,
        ANALYSIS_SYSTEM_PROMPT,
        CODING_SYSTEM_PROMPT,
        REVIEWER_SYSTEM_PROMPT,
        REPORT_SYSTEM_PROMPT,
    ]
    for prompt in prompts:
        assert isinstance(prompt, str)
        assert len(prompt) > 100
        assert "CORE RESPONSIBILITIES" in prompt
        assert "OPERATIONAL BOUNDARIES" in prompt

    # Ensure Researcher explicitly mentions no direct web access
    assert "internet search or web browsing capabilities" in RESEARCHER_SYSTEM_PROMPT
    # Ensure Coding agent explicitly forbids shell execution
    assert "You do NOT have execution privileges" in CODING_SYSTEM_PROMPT


# ------------------------------------------------------------------------------
# 5. Agent Registry Tests
# ------------------------------------------------------------------------------


def test_agent_registry_standard_agents():
    """Verify that get_agent_registry pre-registers all 7 specialized agents."""
    registry = get_agent_registry()
    expected_ids = {
        "supervisor",
        "planner",
        "researcher",
        "analyst",
        "coder",
        "reviewer",
        "reporter",
    }

    agents = registry.list_agents()
    registered_ids = {a.agent_id for a in agents}
    assert expected_ids.issubset(registered_ids)

    for agent_id in expected_ids:
        assert registry.has_agent(agent_id)
        metadata = registry.get_agent_metadata(agent_id)
        assert metadata.agent_id == agent_id
        assert metadata.output_schema_name is not None


def test_agent_registry_prevent_duplicate_and_invalid():
    """Verify registry rejects duplicate registrations and non-BaseAgent classes."""
    registry = AgentRegistry()
    registry.register(SupervisorAgent)

    # Duplicate registration fails
    with pytest.raises(AgentRegistrationError) as exc_dup:
        registry.register(SupervisorAgent)
    assert "already registered" in str(exc_dup.value)

    # Non-BaseAgent registration fails
    class NotAnAgent:
        pass

    with pytest.raises(AgentRegistrationError):
        registry.register(NotAnAgent)  # type: ignore[arg-type]

    # Query for nonexistent agent fails
    with pytest.raises(AgentNotFoundError):
        registry.get_agent_class("nonexistent_agent")


# ------------------------------------------------------------------------------
# 6. Agent Factory Tests
# ------------------------------------------------------------------------------


def test_agent_factory_creation(mock_llm_service: MagicMock):
    """Verify AgentFactory creates agents with dependencies and custom configuration."""
    registry = get_agent_registry()
    factory = AgentFactory(registry=registry, llm_service=mock_llm_service)

    custom_config = AgentConfig(temperature=0.7, max_output_tokens=2048)
    agent = factory.create_agent("coder", config=custom_config)

    assert isinstance(agent, CodingAgent)
    assert agent.llm_service == mock_llm_service
    assert agent.config.temperature == 0.7
    assert agent.config.max_output_tokens == 2048

    with pytest.raises(AgentNotFoundError):
        factory.create_agent("unknown_agent")


# ------------------------------------------------------------------------------
# 7. Controlled Tool Registry Tests
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_registry_registration_and_execution():
    """Verify ToolRegistry registers, lists, and executes controlled tools."""
    tool_registry = ControlledToolRegistry()

    tool_def = ToolDefinition(
        name="calculate_sum",
        description="Adds two integers",
        input_schema={"a": "int", "b": "int"},
        permission=ToolPermission.COMPUTE,
    )

    def add_handler(a: int, b: int) -> int:
        return a + b

    tool_registry.register_tool(tool_def, handler=add_handler)

    assert tool_registry.has_tool("calculate_sum")
    retrieved = tool_registry.get_tool("calculate_sum")
    assert retrieved.name == "calculate_sum"
    assert retrieved.permission == ToolPermission.COMPUTE

    result = await tool_registry.execute_tool("calculate_sum", {"a": 10, "b": 32})
    assert result == 42

    # Duplicate registration fails
    with pytest.raises(ToolRegistrationError):
        tool_registry.register_tool(tool_def)

    # Nonexistent tool execution fails
    with pytest.raises(ToolRegistrationError):
        await tool_registry.execute_tool("unknown_tool", {})


# ------------------------------------------------------------------------------
# 8. Agent Execution Service & DB Persistence Tests
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_execution_service(mock_llm_service: MagicMock, sample_usage: LLMUsage):
    """Verify AgentExecutionService coordinates execution and persists audit records."""
    mock_result = SupervisorResult(
        task_classification="research",
        complexity_assessment="low",
        required_capabilities=["synthesis"],
        recommended_agents=["researcher"],
        delegation_strategy="Researcher synthesizes findings.",
        reasoning_summary="Standard factual query.",
    )

    mock_llm_service.generate_structured = AsyncMock(
        return_value=LLMResponse(
            content="{}",
            parsed=mock_result,
            usage=sample_usage,
            model="gemini-2.5-flash",
        )
    )

    factory = AgentFactory(llm_service=mock_llm_service)
    service = AgentExecutionService(factory=factory)

    mock_session = AsyncMock(spec=AsyncSession)

    input_data = AgentInput(
        task_description="Explain Raft consensus protocol.",
        context={"domain": "distributed_systems"},
    )

    output = await service.execute_agent(
        agent_id="supervisor",
        input_data=input_data,
        session=mock_session,
    )

    assert output.status == AgentExecutionStatus.SUCCESS
    assert output.agent_id == "supervisor"
    # Ensure session.add and session.flush were invoked to persist AgentExecution
    assert mock_session.add.called
    assert mock_session.flush.called


@pytest.mark.asyncio
async def test_agent_execution_service_invalid_agent_raises():
    """Verify AgentExecutionService raises AgentNotFoundError for unmapped agent_id."""
    service = get_agent_execution_service()
    with pytest.raises(AgentNotFoundError):
        await service.execute_agent(
            agent_id="nonexistent_agent_xyz",
            input_data=AgentInput(task_description="Valid task"),
        )
