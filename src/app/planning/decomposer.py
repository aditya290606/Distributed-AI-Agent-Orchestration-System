"""Dynamic task decomposition engine interacting with Planner Agent and LLM service."""

import logging
import uuid
from typing import Any

from app.agents.contracts import AgentExecutionStatus, AgentInput
from app.agents.registry import AgentRegistry, get_agent_registry
from app.planning.matcher import AgentCapabilityMatcher
from app.schemas.planning import (
    ExecutionPlan,
    PlannedTask,
    PlanningRequest,
    PlanStatus,
    TaskPriority,
)
from app.services.agent_execution_service import (
    AgentExecutionService,
    get_agent_execution_service,
)

logger = logging.getLogger("app.planning.decomposer")


class TaskDecomposer:
    """Interacts with the registered PlannerAgent to decompose objectives into planned tasks."""

    def __init__(
        self,
        execution_service: AgentExecutionService | None = None,
        matcher: AgentCapabilityMatcher | None = None,
        registry: AgentRegistry | None = None,
    ) -> None:
        self.registry = registry or get_agent_registry()
        self.execution_service = execution_service or get_agent_execution_service()
        self.matcher = matcher or AgentCapabilityMatcher(self.registry)

    async def decompose_request(
        self,
        request: PlanningRequest,
        iteration: int = 1,
        prior_validation_errors: list[str] | None = None,
    ) -> ExecutionPlan:
        """Decompose a PlanningRequest into an ExecutionPlan via the Planner Agent.

        Args:
            request: The planning request containing objective and constraints.
            iteration: Current planning iteration count.
            prior_validation_errors: Optional validation error strings from a prior iteration.

        Returns:
            ExecutionPlan populated with PlannedTask items.
        """
        logger.info(
            "Executing task decomposition iteration %d for objective: '%s'",
            iteration,
            request.user_request[:80],
        )

        # Build context including constraints and prior repair feedback
        context: dict[str, Any] = {
            "user_request": request.user_request,
            "constraints": request.constraints,
            "max_tasks": request.max_tasks,
            "domain_context": request.context,
            "available_agents": [
                {
                    "agent_id": p.agent_id,
                    "name": p.name,
                    "role": p.role,
                    "supported_task_types": p.supported_task_types,
                }
                for p in self.matcher.list_all_profiles()
            ],
        }

        instructions = (
            f"Decompose the user objective into at most {request.max_tasks} discrete subtasks. "
            "Assign each task to an appropriate registered agent from the available_agents list. "
            "Explicitly define dependencies between steps by step_number (e.g. dependencies: [1]). "
            "Identify tasks that can safely execute concurrently (independent dependencies)."
        )

        if prior_validation_errors:
            error_bullets = "\n".join(f"- {e}" for e in prior_validation_errors)
            instructions += f"\n\nIMPORTANT: The previous plan had validation failures. You MUST fix them:\n{error_bullets}"

        # Invoke the existing Planner Agent through the centralized execution service
        agent_input = AgentInput(
            workflow_id=request.workflow_id,
            task_description=request.user_request,
            context=context,
            instructions=instructions,
            expected_output="Structured PlannerResult with discrete execution steps and dependencies",
        )

        output = await self.execution_service.execute_agent(
            agent_id="planner",
            agent_input=agent_input,
        )

        if output.status != AgentExecutionStatus.SUCCESS or not isinstance(output.result, dict):
            logger.warning(
                "Planner agent execution returned non-success (%s): %s",
                output.status,
                output.error,
            )
            # Create a fallback minimal plan
            return self._create_fallback_plan(
                request, output.error or "Planner agent execution failed"
            )

        result_dict = output.result
        steps_raw = result_dict.get("steps", [])

        # Transform steps into strongly-typed PlannedTask objects
        planned_tasks: list[PlannedTask] = []
        step_number_to_task_id: dict[int, str] = {}

        # 1. First pass: establish task_ids (e.g. 'task_1', 'task_2', etc.)
        for idx, step in enumerate(steps_raw, start=1):
            step_num = step.get("step_number", idx)
            task_id = f"task_{step_num}"
            step_number_to_task_id[step_num] = task_id

        # 2. Second pass: build PlannedTask with mapped dependencies and agent capability matching
        for idx, step in enumerate(steps_raw, start=1):
            step_num = step.get("step_number", idx)
            task_id = step_number_to_task_id.get(step_num, f"task_{idx}")
            task_name = step.get("task_name", f"Task {step_num}")
            suggested_agent = step.get("assigned_agent", "researcher")
            raw_deps = step.get("dependencies", [])

            # Map integer step numbers to string task_ids
            mapped_deps: list[str] = []
            for dep in raw_deps:
                if isinstance(dep, int) and dep in step_number_to_task_id:
                    mapped_deps.append(step_number_to_task_id[dep])
                elif isinstance(dep, str) and dep.strip():
                    mapped_deps.append(dep.strip())

            # Capability matching
            match = self.matcher.match_task(
                task_type=suggested_agent,
                required_capabilities=[],
                preferred_agent_id=suggested_agent,
            )

            task_obj = PlannedTask(
                task_id=task_id,
                name=task_name,
                description=step.get("task_name", task_name),
                task_type=suggested_agent,
                required_capabilities=match.matched_capabilities,
                recommended_agent_id=match.matched_agent_id,
                dependencies=mapped_deps,
                priority=TaskPriority.MEDIUM,
                expected_output=step.get("expected_output", "Verified deliverable"),
                estimated_complexity="moderate",
                execution_constraints={},
            )
            planned_tasks.append(task_obj)

        plan = ExecutionPlan(
            plan_id=str(uuid.uuid4()),
            workflow_id=request.workflow_id,
            objective=result_dict.get("objective", request.user_request),
            tasks=planned_tasks,
            estimated_task_count=len(planned_tasks),
            plan_status=PlanStatus.DRAFT,
            validation_errors=[],
            metadata={
                "iteration": iteration,
                "planning_duration_ms": output.execution_metadata.get("duration_ms", 0),
                "planner_reasoning": output.reasoning_summary,
            },
        )

        return plan

    def _create_fallback_plan(self, request: PlanningRequest, error_msg: str) -> ExecutionPlan:
        """Create a resilient single-step fallback plan if planner agent fails."""
        fallback_task = PlannedTask(
            task_id="task_1",
            name="General Task Execution",
            description=request.user_request,
            task_type="research",
            required_capabilities=["information_synthesis"],
            recommended_agent_id="researcher",
            dependencies=[],
            priority=TaskPriority.HIGH,
            expected_output="Direct task response",
        )
        return ExecutionPlan(
            plan_id=str(uuid.uuid4()),
            workflow_id=request.workflow_id,
            objective=request.user_request,
            tasks=[fallback_task],
            estimated_task_count=1,
            plan_status=PlanStatus.DRAFT,
            validation_errors=[f"Fallback plan generated due to error: {error_msg}"],
            metadata={"is_fallback": True, "error": error_msg},
        )
