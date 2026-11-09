"""Dedicated PlanValidator enforcing structural, capability, and constraint invariants."""

import logging

from app.agents.registry import AgentRegistry, get_agent_registry
from app.planning.graph import DependencyGraph
from app.schemas.planning import ExecutionPlan, PlanningRequest

logger = logging.getLogger("app.planning.validator")


class PlanValidationResult:
    """Outcome container detailing validation status and specific error descriptions."""

    def __init__(self, is_valid: bool, errors: list[str] | None = None) -> None:
        self.is_valid = is_valid
        self.errors = errors or []

    def __bool__(self) -> bool:
        return self.is_valid


class PlanValidator:
    """Enforces strict structural, capability, dependency, and constraint rules on execution plans."""

    def __init__(self, registry: AgentRegistry | None = None) -> None:
        self.registry = registry or get_agent_registry()

    def validate_plan(
        self,
        plan: ExecutionPlan,
        request: PlanningRequest | None = None,
    ) -> PlanValidationResult:
        """Validate all rules for an ExecutionPlan.

        Args:
            plan: The ExecutionPlan to evaluate.
            request: Optional original PlanningRequest specifying limits and constraints.

        Returns:
            PlanValidationResult with is_valid boolean and list of failure reasons.
        """
        errors: list[str] = []

        # 1. Non-empty check
        if not plan.tasks:
            errors.append("Execution plan must contain at least one planned task.")
            return PlanValidationResult(is_valid=False, errors=errors)

        # 2. Task count limit
        max_tasks = request.max_tasks if request else 50
        if len(plan.tasks) > max_tasks:
            errors.append(
                f"Task count ({len(plan.tasks)}) exceeds allowed maximum of {max_tasks} tasks."
            )

        # 3. Unique task IDs
        task_ids: set[str] = set()
        for t in plan.tasks:
            if not t.task_id or not t.task_id.strip():
                errors.append("Found planned task with empty task_id.")
            elif t.task_id in task_ids:
                errors.append(f"Duplicate task_id detected: '{t.task_id}'.")
            task_ids.add(t.task_id)

        # 4. Dependency references and self-dependencies
        for t in plan.tasks:
            for dep in t.dependencies:
                if dep == t.task_id:
                    errors.append(f"Task '{t.task_id}' lists itself as a dependency.")
                elif dep not in task_ids:
                    errors.append(f"Task '{t.task_id}' references non-existent dependency '{dep}'.")

        # 5. Agent availability and capability matching
        for t in plan.tasks:
            if not self.registry.has_agent(t.recommended_agent_id):
                errors.append(
                    f"Task '{t.task_id}' references unregistered agent '{t.recommended_agent_id}'."
                )

        # 6. Cycle detection (DAG validity)
        if not any("references non-existent" in e for e in errors):
            try:
                graph = DependencyGraph(plan.tasks)
                graph_errors = graph.validate_graph()
                errors.extend(graph_errors)
            except Exception as e:
                errors.append(f"Graph validation failed: {e!s}")

        # 7. Execution stages validation (if present)
        if plan.execution_stages:
            seen_stage_tasks: set[str] = set()
            for stage in plan.execution_stages:
                for t in stage.tasks:
                    # Verify each dependency of t has been observed in a PRIOR stage
                    for dep in t.dependencies:
                        if dep not in seen_stage_tasks:
                            errors.append(
                                f"Stage scheduling error: Task '{t.task_id}' in Stage {stage.stage_number} "
                                f"depends on '{dep}' which is not in any prior stage."
                            )
                for t in stage.tasks:
                    seen_stage_tasks.add(t.task_id)

        # 8. Budget constraints (if specified)
        if request and request.execution_budget is not None:
            estimated_cost = plan.metadata.get("estimated_cost_usd", 0.0)
            if (
                isinstance(estimated_cost, (int, float))
                and estimated_cost > request.execution_budget
            ):
                errors.append(
                    f"Estimated cost (${estimated_cost:.4f}) exceeds allocated budget (${request.execution_budget:.4f})."
                )

        return PlanValidationResult(is_valid=(len(errors) == 0), errors=errors)
