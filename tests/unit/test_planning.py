"""Unit tests for Dynamic Planning, DAG Dependency Engine, Validation, Optimization, and LangGraph integration."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.contracts import AgentExecutionStatus, AgentOutput
from app.planning.graph import (
    DependencyCycleError,
    DependencyGraph,
    MissingDependencyError,
)
from app.planning.matcher import AgentCapabilityMatcher
from app.planning.optimizer import PlanOptimizer
from app.planning.prioritizer import TaskPrioritizer
from app.planning.stages import StageGenerator
from app.planning.validator import PlanValidator
from app.schemas.planning import (
    ExecutionPlan,
    PlannedTask,
    PlanningRequest,
    PlanStatus,
    TaskPriority,
)
from app.services.agent_execution_service import AgentExecutionService
from app.services.planning_service import PlanningService

# ------------------------------------------------------------------------------
# 1. Planning Schema Validation Tests
# ------------------------------------------------------------------------------


def test_planned_task_validation():
    """Verify PlannedTask validates attributes and rejects self-dependencies."""
    task = PlannedTask(
        task_id="task_1",
        name="Research Authentication",
        description="Investigate OAuth2 flows",
        task_type="research",
        recommended_agent_id="researcher",
        dependencies=[],
        expected_output="Auth recommendations",
    )
    assert task.task_id == "task_1"
    assert task.priority == TaskPriority.MEDIUM

    # Reject self-dependency
    with pytest.raises(ValueError, match="cannot list itself as a dependency"):
        PlannedTask(
            task_id="task_bad",
            name="Bad Task",
            description="Bad",
            task_type="coding",
            recommended_agent_id="coder",
            dependencies=["task_bad"],
            expected_output="None",
        )


def test_execution_plan_validation():
    """Verify ExecutionPlan checks duplicate task IDs and missing dependency references."""
    t1 = PlannedTask(
        task_id="t1",
        name="T1",
        description="D1",
        task_type="research",
        recommended_agent_id="researcher",
        dependencies=[],
        expected_output="O1",
    )
    t2 = PlannedTask(
        task_id="t2",
        name="T2",
        description="D2",
        task_type="coding",
        recommended_agent_id="coder",
        dependencies=["t1"],
        expected_output="O2",
    )

    plan = ExecutionPlan(
        objective="Build Auth",
        tasks=[t1, t2],
    )
    assert len(plan.tasks) == 2
    assert plan.estimated_task_count == 2

    # Duplicate task_id
    with pytest.raises(ValueError, match="Duplicate task_id detected"):
        ExecutionPlan(
            objective="Duplicate Test",
            tasks=[t1, t1],
        )

    # Missing dependency reference
    t3 = PlannedTask(
        task_id="t3",
        name="T3",
        description="D3",
        task_type="review",
        recommended_agent_id="reviewer",
        dependencies=["non_existent_task"],
        expected_output="O3",
    )
    with pytest.raises(ValueError, match="references unknown dependency"):
        ExecutionPlan(
            objective="Missing Dep Test",
            tasks=[t1, t3],
        )


# ------------------------------------------------------------------------------
# 2. Agent Capability Matching Tests
# ------------------------------------------------------------------------------


def test_agent_capability_matching():
    """Verify AgentCapabilityMatcher selects valid registered agents with confidence and rationale."""
    matcher = AgentCapabilityMatcher()

    # Deterministic match for preferred coder
    match_coder = matcher.match_task(
        task_type="coding",
        required_capabilities=["code_generation"],
        preferred_agent_id="coder",
    )
    assert match_coder.matched_agent_id == "coder"
    assert match_coder.confidence >= 0.85
    assert "coder" in match_coder.selection_reason

    # Match by task type and capability without preferred agent
    match_research = matcher.match_task(
        task_type="research",
        required_capabilities=["knowledge_retrieval"],
    )
    assert match_research.matched_agent_id == "researcher"
    assert "researcher" in match_research.matched_capabilities or match_research.confidence > 0.5

    # Match analysis task
    match_analysis = matcher.match_task(task_type="analysis")
    assert match_analysis.matched_agent_id == "analyst"

    # Unknown task type falls back to registered generalist
    match_unknown = matcher.match_task(task_type="quantum_computing_teleportation")
    assert match_unknown.matched_agent_id in ("researcher", "supervisor")
    assert match_unknown.confidence <= 0.5


# ------------------------------------------------------------------------------
# 3. Dependency Graph & Topological Sorting Tests
# ------------------------------------------------------------------------------


def test_dependency_graph_acyclic_and_topological_sort():
    """Verify DependencyGraph topological sort and parallel level extraction on valid DAG."""
    # A -> B -> D
    # A -> C -> D
    tA = PlannedTask(
        task_id="A",
        name="A",
        description="A",
        task_type="research",
        recommended_agent_id="researcher",
        dependencies=[],
        expected_output="A",
    )
    tB = PlannedTask(
        task_id="B",
        name="B",
        description="B",
        task_type="analysis",
        recommended_agent_id="analyst",
        dependencies=["A"],
        expected_output="B",
    )
    tC = PlannedTask(
        task_id="C",
        name="C",
        description="C",
        task_type="coding",
        recommended_agent_id="coder",
        dependencies=["A"],
        expected_output="C",
    )
    tD = PlannedTask(
        task_id="D",
        name="D",
        description="D",
        task_type="review",
        recommended_agent_id="reviewer",
        dependencies=["B", "C"],
        expected_output="D",
    )

    graph = DependencyGraph([tA, tB, tC, tD])
    assert len(graph.validate_graph()) == 0

    roots = graph.get_root_tasks()
    assert [r.task_id for r in roots] == ["A"]

    terminals = graph.get_terminal_tasks()
    assert [t.task_id for t in terminals] == ["D"]

    order = graph.topological_sort()
    assert order.index("A") < order.index("B")
    assert order.index("A") < order.index("C")
    assert order.index("B") < order.index("D")
    assert order.index("C") < order.index("D")

    levels = graph.get_parallel_levels()
    assert levels == [["A"], ["B", "C"], ["D"]]


def test_dependency_graph_cycle_detection():
    """Verify DependencyGraph detects circular dependencies and raises DependencyCycleError."""
    # A -> B -> C -> A
    tA = PlannedTask(
        task_id="A",
        name="A",
        description="A",
        task_type="research",
        recommended_agent_id="researcher",
        dependencies=["C"],
        expected_output="A",
    )
    tB = PlannedTask(
        task_id="B",
        name="B",
        description="B",
        task_type="analysis",
        recommended_agent_id="analyst",
        dependencies=["A"],
        expected_output="B",
    )
    tC = PlannedTask(
        task_id="C",
        name="C",
        description="C",
        task_type="coding",
        recommended_agent_id="coder",
        dependencies=["B"],
        expected_output="C",
    )

    # Bypass ExecutionPlan validator by directly populating graph
    graph = DependencyGraph([tA, tB, tC])
    errors = graph.validate_graph()
    assert any("Circular dependency detected" in e for e in errors)

    with pytest.raises(DependencyCycleError, match="Circular dependency detected"):
        graph.topological_sort()


def test_dependency_graph_missing_dependency():
    """Verify DependencyGraph detects missing dependency references."""
    tA = PlannedTask(
        task_id="A",
        name="A",
        description="A",
        task_type="research",
        recommended_agent_id="researcher",
        dependencies=["GHOST"],
        expected_output="A",
    )
    graph = DependencyGraph([tA])
    errors = graph.validate_graph()
    assert any("references unknown dependency 'GHOST'" in e for e in errors)

    with pytest.raises(MissingDependencyError, match="non-existent task 'GHOST'"):
        graph.topological_sort()


# ------------------------------------------------------------------------------
# 4. Task Prioritization Tests
# ------------------------------------------------------------------------------


def test_task_prioritization():
    """Verify TaskPrioritizer assigns explainable priorities based on critical path length."""
    # A -> B -> C (A blocks 2 tasks, path length 2)
    # D (independent leaf)
    tA = PlannedTask(
        task_id="A",
        name="A",
        description="A",
        task_type="research",
        recommended_agent_id="researcher",
        dependencies=[],
        expected_output="A",
    )
    tB = PlannedTask(
        task_id="B",
        name="B",
        description="B",
        task_type="analysis",
        recommended_agent_id="analyst",
        dependencies=["A"],
        expected_output="B",
    )
    tC = PlannedTask(
        task_id="C",
        name="C",
        description="C",
        task_type="coding",
        recommended_agent_id="coder",
        dependencies=["B"],
        expected_output="C",
    )
    tD = PlannedTask(
        task_id="D",
        name="D",
        description="Independent",
        task_type="report",
        recommended_agent_id="reporter",
        dependencies=[],
        expected_output="D",
    )

    graph = DependencyGraph([tA, tB, tC, tD])
    prioritizer = TaskPrioritizer(graph)
    priorities = prioritizer.prioritize_tasks()

    assert priorities["A"] == TaskPriority.CRITICAL
    assert priorities["B"] in (TaskPriority.HIGH, TaskPriority.MEDIUM)
    assert priorities["C"] == TaskPriority.LOW
    assert priorities["D"] == TaskPriority.LOW


# ------------------------------------------------------------------------------
# 5. Execution Stage Generation Tests
# ------------------------------------------------------------------------------


def test_execution_stage_generation():
    """Verify StageGenerator partitions tasks into sequential stages and enforces concurrency limits."""
    # Roots: A, B, C, D
    tasks = [
        PlannedTask(
            task_id=f"T{i}",
            name=f"Task {i}",
            description=f"Desc {i}",
            task_type="research",
            recommended_agent_id="researcher",
            dependencies=[],
            expected_output="Out",
        )
        for i in range(1, 5)
    ]
    graph = DependencyGraph(tasks)
    stage_gen = StageGenerator(graph)

    # Concurrency limit of 2 partitions 4 root tasks into 2 stages
    stages = stage_gen.generate_stages(max_concurrency=2)
    assert len(stages) == 2
    assert stages[0].stage_number == 1
    assert len(stages[0].tasks) == 2
    assert stages[1].stage_number == 2
    assert len(stages[1].tasks) == 2


# ------------------------------------------------------------------------------
# 6. Plan Validation & Repair Tests
# ------------------------------------------------------------------------------


def test_plan_validator():
    """Verify PlanValidator checks limits, cycles, and agent registrations."""
    validator = PlanValidator()

    t1 = PlannedTask(
        task_id="t1",
        name="T1",
        description="D1",
        task_type="research",
        recommended_agent_id="researcher",
        dependencies=[],
        expected_output="O1",
    )
    plan = ExecutionPlan(objective="Test", tasks=[t1])

    req = PlanningRequest(user_request="Test", max_tasks=1)
    res = validator.validate_plan(plan, req)
    assert res.is_valid is True

    # Exceed max tasks limit
    t2 = PlannedTask(
        task_id="t2",
        name="T2",
        description="D2",
        task_type="coding",
        recommended_agent_id="coder",
        dependencies=["t1"],
        expected_output="O2",
    )
    plan2 = ExecutionPlan(objective="Test2", tasks=[t1, t2])
    res2 = validator.validate_plan(plan2, req)
    assert res2.is_valid is False
    assert any("exceeds allowed maximum of 1" in e for e in res2.errors)


# ------------------------------------------------------------------------------
# 7. Plan Optimization Tests
# ------------------------------------------------------------------------------


def test_plan_optimizer_transitive_reduction_and_deduplication():
    """Verify PlanOptimizer merges identical tasks and removes redundant transitive dependencies."""
    # Transitive reduction test:
    # A -> B -> C and direct A -> C. The direct edge A -> C is transitively redundant!
    tA = PlannedTask(
        task_id="tA",
        name="Task A",
        description="Desc A",
        task_type="research",
        recommended_agent_id="researcher",
        dependencies=[],
        expected_output="A",
    )
    tB = PlannedTask(
        task_id="tB",
        name="Task B",
        description="Desc B",
        task_type="analysis",
        recommended_agent_id="analyst",
        dependencies=["tA"],
        expected_output="B",
    )
    tC = PlannedTask(
        task_id="tC",
        name="Task C",
        description="Desc C",
        task_type="coding",
        recommended_agent_id="coder",
        dependencies=["tA", "tB"],
        expected_output="C",
    )

    # Duplicate task test: identical to tB
    tB_dup = PlannedTask(
        task_id="tB_dup",
        name="Task B",
        description="Desc B",
        task_type="analysis",
        recommended_agent_id="analyst",
        dependencies=["tA"],
        expected_output="B",
    )

    plan = ExecutionPlan(objective="Optimization Objective", tasks=[tA, tB, tC, tB_dup])

    optimizer = PlanOptimizer()
    optimized_plan, logs = optimizer.optimize_plan(plan)

    # 1. Duplicate task merged
    task_ids = [t.task_id for t in optimized_plan.tasks]
    assert "tB_dup" not in task_ids
    assert len(optimized_plan.tasks) == 3

    # 2. Transitive dependency from tC to tA removed (since tC -> tB -> tA)
    tC_optimized = next(t for t in optimized_plan.tasks if t.task_id == "tC")
    assert "tA" not in tC_optimized.dependencies
    assert tC_optimized.dependencies == ["tB"]
    assert any("Removed redundant transitive dependency" in log for log in logs)


# ------------------------------------------------------------------------------
# 8. Task Decomposer & PlanningService Tests
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planning_service_successful_generation():
    """Verify PlanningService generates, validates, optimizes, and returns an ExecutionPlan."""
    mock_execution_service = MagicMock(spec=AgentExecutionService)

    # Mock Planner agent response
    mock_planner_output = AgentOutput(
        agent_id="planner",
        agent_name="Planner Agent",
        status=AgentExecutionStatus.SUCCESS,
        result={
            "objective": "Build event driven system",
            "total_steps": 2,
            "steps": [
                {
                    "step_number": 1,
                    "task_name": "Research message formats",
                    "assigned_agent": "researcher",
                    "dependencies": [],
                    "expected_output": "Schema recommendations",
                },
                {
                    "step_number": 2,
                    "task_name": "Implement consumers",
                    "assigned_agent": "coder",
                    "dependencies": [1],
                    "expected_output": "FastAPI consumer code",
                },
            ],
            "estimated_effort": "moderate",
            "reasoning_summary": "Decomposed into research and coding.",
        },
        reasoning_summary="Decomposed successfully.",
        execution_metadata={"duration_ms": 250},
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )

    mock_execution_service.execute_agent = AsyncMock(return_value=mock_planner_output)

    service = PlanningService()
    service.decomposer.execution_service = mock_execution_service

    req = PlanningRequest(user_request="Build event driven system with Kafka and FastAPI")
    plan = await service.generate_plan(req)

    assert plan.plan_status == PlanStatus.OPTIMIZED
    assert len(plan.tasks) == 2
    assert plan.tasks[0].task_id == "task_1"
    assert plan.tasks[0].recommended_agent_id == "researcher"
    assert plan.tasks[1].task_id == "task_2"
    assert plan.tasks[1].dependencies == ["task_1"]
    assert len(plan.execution_stages) >= 2


@pytest.mark.asyncio
async def test_planning_service_bounded_repair_loop():
    """Verify PlanningService attempts bounded repair when initial plan has validation failures."""
    mock_execution_service = MagicMock(spec=AgentExecutionService)

    # 1. First iteration: Returns cycle (1 -> 2 -> 1)
    bad_planner_output = AgentOutput(
        agent_id="planner",
        agent_name="Planner Agent",
        status=AgentExecutionStatus.SUCCESS,
        result={
            "objective": "Broken Cycle Plan",
            "total_steps": 2,
            "steps": [
                {
                    "step_number": 1,
                    "task_name": "Step 1",
                    "assigned_agent": "researcher",
                    "dependencies": [2],
                    "expected_output": "Out 1",
                },
                {
                    "step_number": 2,
                    "task_name": "Step 2",
                    "assigned_agent": "coder",
                    "dependencies": [1],
                    "expected_output": "Out 2",
                },
            ],
            "estimated_effort": "moderate",
            "reasoning_summary": "Initial attempt with cycle.",
        },
        reasoning_summary="Attempt 1.",
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )

    # 2. Second iteration: Repaired acyclic plan (1 -> 2)
    repaired_planner_output = AgentOutput(
        agent_id="planner",
        agent_name="Planner Agent",
        status=AgentExecutionStatus.SUCCESS,
        result={
            "objective": "Repaired Plan",
            "total_steps": 2,
            "steps": [
                {
                    "step_number": 1,
                    "task_name": "Step 1",
                    "assigned_agent": "researcher",
                    "dependencies": [],
                    "expected_output": "Out 1",
                },
                {
                    "step_number": 2,
                    "task_name": "Step 2",
                    "assigned_agent": "coder",
                    "dependencies": [1],
                    "expected_output": "Out 2",
                },
            ],
            "estimated_effort": "moderate",
            "reasoning_summary": "Repaired dependency cycle.",
        },
        reasoning_summary="Attempt 2 repaired.",
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )

    mock_execution_service.execute_agent = AsyncMock(
        side_effect=[bad_planner_output, repaired_planner_output]
    )

    service = PlanningService()
    service.decomposer.execution_service = mock_execution_service

    req = PlanningRequest(user_request="Build with repair", max_planning_iterations=3)
    plan = await service.generate_plan(req)

    assert plan.plan_status == PlanStatus.OPTIMIZED
    assert len(plan.tasks) == 2
    assert plan.metadata.get("iterations_count") == 2
    assert mock_execution_service.execute_agent.call_count == 2


@pytest.mark.asyncio
async def test_langgraph_planning_and_validation_workflow():
    """Verify LangGraph workflow coordinates Supervisor -> Planner -> Plan Validator -> Specialist."""
    from app.orchestration.checkpointer import WorkflowCheckpointAdapter
    from app.orchestration.graph import build_workflow_graph
    from app.orchestration.nodes import OrchestrationNodes
    from app.orchestration.state import create_initial_state

    mock_service = MagicMock(spec=AgentExecutionService)

    async def _mock_agent(agent_id: str, *args: Any, **kwargs: Any):
        del args, kwargs
        if agent_id == "supervisor":
            res = {
                "task_classification": "complex",
                "recommended_agents": ["planner", "researcher", "coder"],
                "reasoning_summary": "Complex task requiring full planning.",
            }
        elif agent_id == "planner":
            res = {
                "objective": "Build event service",
                "total_steps": 1,
                "steps": [
                    {
                        "step_number": 1,
                        "task_name": "Research API",
                        "assigned_agent": "researcher",
                        "dependencies": [],
                        "expected_output": "API specs",
                    }
                ],
                "estimated_effort": "low",
                "reasoning_summary": "Single step research.",
            }
        elif agent_id == "researcher":
            res = {"findings": ["API verified"], "reasoning_summary": "Researched."}
        elif agent_id == "reviewer":
            res = {"verdict": "ACCEPT", "reasoning_summary": "Accepted."}
        elif agent_id == "reporter":
            res = {"report_title": "Final Summary", "reasoning_summary": "Reported."}
        else:
            res = {}

        return AgentOutput(
            agent_id=agent_id,
            agent_name=agent_id.capitalize(),
            status=AgentExecutionStatus.SUCCESS,
            result=res,
            reasoning_summary=f"{agent_id} completed.",
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )

    mock_service.execute_agent = AsyncMock(side_effect=_mock_agent)

    nodes = OrchestrationNodes(execution_service=mock_service)
    checkpointer = WorkflowCheckpointAdapter()
    graph = build_workflow_graph(nodes=nodes, checkpointer=checkpointer.checkpointer)

    initial_state = create_initial_state(
        workflow_id="wf-planning-e2e",
        user_request="Plan and research event service",
    )
    config = checkpointer.build_config("wf-planning-e2e")

    final_state = await graph.ainvoke(initial_state, config=config)

    assert final_state["execution_status"] == "COMPLETED"
    assert "planner" in final_state["completed_agents"]
    assert "researcher" in final_state["completed_agents"]
    assert "reviewer" in final_state["completed_agents"]
    assert "reporter" in final_state["completed_agents"]
    assert final_state["execution_plan"] is not None
