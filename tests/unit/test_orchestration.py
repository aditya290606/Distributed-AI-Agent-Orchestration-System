"""Unit tests for LangGraph multi-agent orchestration, state, routing, and nodes."""

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.contracts import AgentExecutionStatus, AgentOutput
from app.db.models.workflow import Workflow, WorkflowStatus
from app.orchestration.checkpointer import WorkflowCheckpointAdapter
from app.orchestration.executor import WorkflowExecutor
from app.orchestration.graph import build_workflow_graph
from app.orchestration.nodes import OrchestrationNodes
from app.orchestration.router import (
    route_after_planner,
    route_after_reviewer,
    route_after_specialist,
    route_after_supervisor,
)
from app.orchestration.state import (
    NexusState,
    append_unique,
    create_initial_state,
    merge_dicts,
)
from app.services.agent_execution_service import AgentExecutionService

# ------------------------------------------------------------------------------
# 1. State and Reducers Tests
# ------------------------------------------------------------------------------


def test_state_creation_and_defaults():
    """Verify create_initial_state initializes all fields with correct types and defaults."""
    state = create_initial_state(
        workflow_id="wf-123",
        user_request="Build a search service",
        task_description="Detailed task description",
        max_revisions=2,
    )
    assert state["workflow_id"] == "wf-123"
    assert state["user_request"] == "Build a search service"
    assert state["task_description"] == "Detailed task description"
    assert state["execution_status"] == "INITIALIZED"
    assert state["max_revisions"] == 2
    assert state["revision_count"] == 0
    assert state["selected_agents"] == []
    assert state["completed_agents"] == []
    assert state["agent_results"] == {}
    assert state["errors"] == []


def test_state_reducers_merge_dicts_and_append_unique():
    """Verify merge_dicts and append_unique handle parallel state accumulation safely."""
    # merge_dicts reducer
    d1 = {"researcher": {"findings": ["doc1"]}}
    d2 = {"analyst": {"insights": ["trend1"]}}
    merged = merge_dicts(d1, d2)
    assert "researcher" in merged
    assert "analyst" in merged

    # append_unique reducer
    l1 = ["supervisor", "planner"]
    l2 = ["researcher", "planner"]  # planner is duplicate
    combined = append_unique(l1, l2)
    assert combined == ["supervisor", "planner", "researcher"]


# ------------------------------------------------------------------------------
# 2. Conditional Routing Tests
# ------------------------------------------------------------------------------


def test_routing_after_supervisor():
    """Verify route_after_supervisor routes simple tasks directly and complex tasks to planner."""
    # Complex task (multiple agents recommended) -> routes to planner
    state_complex: NexusState = create_initial_state("wf-1", "Build complex system")
    state_complex["selected_agents"] = ["planner", "coder", "reviewer"]
    assert route_after_supervisor(state_complex) == "planner"

    # Simple task (single specialist recommended) -> routes directly to that specialist
    state_simple_code: NexusState = create_initial_state("wf-2", "Write a fibonacci function")
    state_simple_code["selected_agents"] = ["coder"]
    assert route_after_supervisor(state_simple_code) == "coder"

    state_simple_research: NexusState = create_initial_state("wf-3", "What is Raft consensus?")
    state_simple_research["selected_agents"] = ["researcher"]
    assert route_after_supervisor(state_simple_research) == "researcher"

    # Failure state -> routes to END
    state_failed: NexusState = create_initial_state("wf-4", "Failing task")
    state_failed["execution_status"] = "FAILED"
    assert route_after_supervisor(state_failed) == "__end__"


def test_routing_after_planner():
    """Verify route_after_planner initiates parallel fan-out or sequential routing."""
    state: NexusState = create_initial_state("wf-1", "Plan execution")
    state["pending_agents"] = ["researcher", "analyst", "coder"]

    # When researcher, analyst, and coder are pending, fan out researcher & analyst in parallel
    next_step = route_after_planner(state)
    assert next_step == ["researcher", "analyst"]

    # Sequential routing
    state["pending_agents"] = ["coder"]
    assert route_after_planner(state) == "coder"

    # Empty pending agents -> transitions to reviewer
    state["pending_agents"] = []
    assert route_after_planner(state) == "reviewer"


def test_routing_after_specialist():
    """Verify route_after_specialist cycles through pending agents and terminates at reviewer."""
    state: NexusState = create_initial_state("wf-1", "Specialist step")
    state["pending_agents"] = ["coder"]
    assert route_after_specialist(state) == "coder"

    state["pending_agents"] = []
    assert route_after_specialist(state) == "reviewer"


def test_routing_after_reviewer_acceptance_and_revision_limits():
    """Verify route_after_reviewer evaluates acceptance, revision loops, and threshold limits."""
    state: NexusState = create_initial_state("wf-1", "Review step", max_revisions=2)

    # 1. Acceptance -> proceeds to reporter
    state["review_result"] = {"verdict": "ACCEPT", "meets_requirements": True}
    assert route_after_reviewer(state) == "reporter"

    # 2. Revision needed (within limit) -> loops back to coder
    state["review_result"] = {"verdict": "REVISE", "meets_requirements": False}
    state["completed_agents"] = ["supervisor", "planner", "coder"]
    state["revision_count"] = 1
    assert route_after_reviewer(state) == "coder"

    # 3. Maximum revisions reached -> forced exit to reporter with limits noted
    state["revision_count"] = 2
    assert route_after_reviewer(state) == "reporter"


# ------------------------------------------------------------------------------
# 3. Orchestration Nodes Execution Tests
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supervisor_node_execution():
    """Verify supervisor_node classifies task and updates state correctly."""
    mock_service = MagicMock(spec=AgentExecutionService)
    mock_service.execute_agent = AsyncMock(
        return_value=AgentOutput(
            agent_id="supervisor",
            agent_name="Supervisor Agent",
            status=AgentExecutionStatus.SUCCESS,
            result={
                "task_classification": "architecture",
                "complexity_assessment": "high",
                "recommended_agents": ["planner", "coder", "reviewer"],
                "delegation_strategy": "Plan then code and review",
                "reasoning_summary": "Architecture task decomposition.",
            },
            reasoning_summary="Architecture task decomposition.",
            execution_metadata={"tokens_used": 120},
            started_at=MagicMock(),
            completed_at=MagicMock(),
        )
    )

    nodes = OrchestrationNodes(execution_service=mock_service)
    state = create_initial_state("wf-test", "Design distributed pipeline")

    updates = await nodes.supervisor_node(state)

    assert updates["current_agent"] == "supervisor"
    assert "supervisor" in updates["completed_agents"]
    assert updates["execution_status"] == "SUPERVISED"
    assert updates["pending_agents"] == ["planner"]
    assert "supervisor" in updates["agent_results"]


@pytest.mark.asyncio
async def test_planner_node_execution():
    """Verify planner_node decomposes objective and populates pending_agents."""
    mock_service = MagicMock(spec=AgentExecutionService)
    mock_service.execute_agent = AsyncMock(
        return_value=AgentOutput(
            agent_id="planner",
            agent_name="Planner Agent",
            status=AgentExecutionStatus.SUCCESS,
            result={
                "objective": "Build API",
                "total_steps": 2,
                "execution_order": [1, 2],
                "steps": [
                    {"step_number": 1, "task_name": "Code", "assigned_agent": "coder"},
                    {"step_number": 2, "task_name": "Review", "assigned_agent": "reviewer"},
                ],
                "estimated_effort": "moderate",
                "reasoning_summary": "Two steps.",
            },
            reasoning_summary="Two steps.",
            execution_metadata={"tokens_used": 140},
            started_at=MagicMock(),
            completed_at=MagicMock(),
        )
    )

    nodes = OrchestrationNodes(execution_service=mock_service)
    state = create_initial_state("wf-test", "Build API")

    updates = await nodes.planner_node(state)

    assert updates["current_agent"] == "planner"
    assert updates["execution_status"] == "PLANNED"
    assert updates["pending_agents"] == ["coder", "reviewer"]
    assert updates["execution_plan"]["total_steps"] == 2


@pytest.mark.asyncio
async def test_reviewer_node_increments_revision_on_revise():
    """Verify reviewer_node increments revision_count when verdict is REVISE."""
    mock_service = MagicMock(spec=AgentExecutionService)
    mock_service.execute_agent = AsyncMock(
        return_value=AgentOutput(
            agent_id="reviewer",
            agent_name="Reviewer Agent",
            status=AgentExecutionStatus.SUCCESS,
            result={
                "target_artifact_name": "code",
                "meets_requirements": False,
                "verdict": "REVISE",
                "findings": [],
                "improvement_recommendations": ["Add type hints"],
                "reasoning_summary": "Type hints missing.",
            },
            reasoning_summary="Type hints missing.",
            execution_metadata={"tokens_used": 110},
            started_at=MagicMock(),
            completed_at=MagicMock(),
        )
    )

    nodes = OrchestrationNodes(execution_service=mock_service)
    state = create_initial_state("wf-test", "Audit code")
    state["revision_count"] = 0

    updates = await nodes.reviewer_node(state)

    assert updates["current_agent"] == "reviewer"
    assert updates["review_result"]["verdict"] == "REVISE"
    assert updates["revision_count"] == 1


# ------------------------------------------------------------------------------
# 4. StateGraph Compilation & End-to-End Orchestration Tests
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workflow_graph_compilation_and_simple_execution():
    """Verify StateGraph compiles and executes a simple workflow with mocked agents."""
    mock_service = MagicMock(spec=AgentExecutionService)

    # Configure mock returns for agents in a simple coding workflow
    async def _mock_execute_agent(agent_id: str, *args: Any, **kwargs: Any):
        del args, kwargs
        if agent_id == "supervisor":
            result = {
                "task_classification": "coding",
                "complexity_assessment": "low",
                "recommended_agents": ["coder"],  # Simple task: single specialist
                "delegation_strategy": "Direct coder execution",
                "reasoning_summary": "Simple task.",
            }
        elif agent_id == "coder":
            result = {
                "technical_solution": "Fibonacci implementation",
                "architecture_notes": "Recursive with memoization",
                "code_blocks": [{"language": "python", "code": "def fib(n): pass"}],
                "edge_cases": ["Negative numbers"],
                "testing_recommendations": ["Test base cases"],
                "reasoning_summary": "Implemented cleanly.",
            }
        elif agent_id == "reviewer":
            result = {
                "target_artifact_name": "fib",
                "meets_requirements": True,
                "verdict": "ACCEPT",
                "findings": [],
                "improvement_recommendations": [],
                "reasoning_summary": "Approved without revisions.",
            }
        elif agent_id == "reporter":
            result = {
                "report_title": "Fibonacci Implementation Report",
                "executive_summary": "Completed successfully.",
                "sections": [{"title": "Code", "content": "Clean code", "key_takeaways": []}],
                "conclusions": ["Ready for production"],
                "missing_evidence": [],
                "reasoning_summary": "Synthesized deliverables.",
            }
        else:
            result = {"generic": "ok"}

        return AgentOutput(
            agent_id=agent_id,
            agent_name=f"{agent_id.capitalize()} Agent",
            status=AgentExecutionStatus.SUCCESS,
            result=result,
            reasoning_summary="Executed successfully.",
            execution_metadata={"tokens_used": 100},
            started_at=MagicMock(),
            completed_at=MagicMock(),
        )

    mock_service.execute_agent = AsyncMock(side_effect=_mock_execute_agent)

    nodes = OrchestrationNodes(execution_service=mock_service)
    checkpointer = WorkflowCheckpointAdapter()
    graph = build_workflow_graph(nodes=nodes, checkpointer=checkpointer.checkpointer)

    initial_state = create_initial_state(
        workflow_id="wf-test-simple",
        user_request="Generate python fibonacci function",
    )
    config = checkpointer.build_config("wf-test-simple")

    final_state = await graph.ainvoke(initial_state, config=config)

    assert final_state["execution_status"] == "COMPLETED"
    assert final_state["final_output"] is not None
    assert final_state["final_output"]["report_title"] == "Fibonacci Implementation Report"
    assert "coder" in final_state["completed_agents"]
    assert "reviewer" in final_state["completed_agents"]
    assert "reporter" in final_state["completed_agents"]


# ------------------------------------------------------------------------------
# 5. WorkflowExecutor Service & DB Synchronization Tests
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workflow_executor_with_db_synchronization():
    """Verify WorkflowExecutor manages lifecycle, creates tasks, and synchronizes status in DB."""
    mock_service = MagicMock(spec=AgentExecutionService)

    async def _mock_execute_agent(agent_id: str, *args: Any, **kwargs: Any):
        del args, kwargs
        if agent_id == "supervisor":
            res = {"recommended_agents": ["coder"], "reasoning_summary": "Simple task"}
        elif agent_id == "coder":
            res = {"code": "print('hello')", "reasoning_summary": "Coded"}
        elif agent_id == "reviewer":
            res = {"verdict": "ACCEPT", "reasoning_summary": "Accepted"}
        elif agent_id == "reporter":
            res = {"report_title": "Final Summary", "reasoning_summary": "Reported"}
        else:
            res = {}

        return AgentOutput(
            agent_id=agent_id,
            agent_name=agent_id,
            status=AgentExecutionStatus.SUCCESS,
            result=res,
            reasoning_summary="Done",
            execution_metadata={"tokens_used": 50},
            started_at=MagicMock(),
            completed_at=MagicMock(),
        )

    mock_service.execute_agent = AsyncMock(side_effect=_mock_execute_agent)

    executor = WorkflowExecutor(execution_service=mock_service)
    mock_session = AsyncMock(spec=AsyncSession)

    mock_workflow = Workflow(
        id=uuid.uuid4(),
        name="Workflow-Hello",
        description="Hello World Script",
        status=WorkflowStatus.PENDING.value,
        input_data={},
    )
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_workflow
    mock_session.execute = AsyncMock(return_value=mock_result)

    result = await executor.execute_workflow(
        user_request="Hello World Script",
        session=mock_session,
    )

    assert result["status"] == WorkflowStatus.COMPLETED.value
    assert result["final_output"] is not None
    assert "coder" in result["completed_agents"]
    assert mock_session.add.called
    assert mock_session.flush.called
