"""Conservative plan optimizer performing transitive dependency reduction and safe parallelization."""

import logging
from collections import defaultdict

from app.planning.graph import DependencyGraph
from app.schemas.planning import ExecutionPlan, PlannedTask

logger = logging.getLogger("app.planning.optimizer")


class PlanOptimizer:
    """Performs conservative, auditable graph optimizations preserving all logical invariants."""

    def optimize_plan(self, plan: ExecutionPlan) -> tuple[ExecutionPlan, list[str]]:
        """Apply conservative optimizations to an ExecutionPlan.

        Optimizations:
        1. Transitive Dependency Reduction: Removes redundant direct edges if an alternate path exists
           (e.g. if A -> B -> C and A -> C, removing A -> C is safe and increases scheduling flexibility).
        2. Deduplication of Identical Tasks: Detects identical subtasks targeting the same agent with same inputs.

        Args:
            plan: Input validated execution plan.

        Returns:
            Tuple of (optimized_plan, list_of_audit_records).
        """
        audit_log: list[str] = []

        if not plan.tasks:
            return plan, audit_log

        # 1. Deduplicate tasks
        tasks, dedup_logs = self._deduplicate_tasks(plan.tasks)
        audit_log.extend(dedup_logs)

        # 2. Transitive reduction of dependencies
        tasks, reduction_logs = self._reduce_transitive_dependencies(tasks)
        audit_log.extend(reduction_logs)

        # Rebuild stages with optimized tasks
        try:
            graph = DependencyGraph(tasks)
            from app.planning.stages import StageGenerator

            stages = StageGenerator(graph).generate_stages()
        except Exception as e:
            logger.warning(
                "Failed to regenerate stages after optimization: %s. Retaining original.", e
            )
            stages = plan.execution_stages

        optimized_metadata = dict(plan.metadata)
        optimizations = list(optimized_metadata.get("optimizations_applied", []))
        optimizations.extend(audit_log)
        optimized_metadata["optimizations_applied"] = optimizations

        optimized_plan = ExecutionPlan(
            plan_id=plan.plan_id,
            workflow_id=plan.workflow_id,
            objective=plan.objective,
            tasks=tasks,
            execution_stages=stages,
            estimated_task_count=len(tasks),
            plan_status=plan.plan_status,
            validation_errors=plan.validation_errors,
            metadata=optimized_metadata,
            created_at=plan.created_at,
        )

        return optimized_plan, audit_log

    def _deduplicate_tasks(self, tasks: list[PlannedTask]) -> tuple[list[PlannedTask], list[str]]:
        """Identify and merge tasks with identical agent, task_type, name, and description."""
        seen: dict[tuple[str, str, str, str], PlannedTask] = {}
        id_replacement: dict[str, str] = {}
        unique_tasks: list[PlannedTask] = []
        logs: list[str] = []

        for t in tasks:
            key = (
                t.recommended_agent_id.lower().strip(),
                t.task_type.lower().strip(),
                t.name.lower().strip(),
                t.description.lower().strip(),
            )
            if key in seen:
                primary = seen[key]
                id_replacement[t.task_id] = primary.task_id
                logs.append(
                    f"Merged duplicate task '{t.task_id}' into primary task '{primary.task_id}' ({t.name})."
                )
            else:
                seen[key] = t
                unique_tasks.append(t)

        # Remap dependencies for remaining tasks
        for t in unique_tasks:
            updated_deps: list[str] = []
            for dep in t.dependencies:
                mapped = id_replacement.get(dep, dep)
                if mapped != t.task_id and mapped not in updated_deps:
                    updated_deps.append(mapped)
            t.dependencies = updated_deps

        return unique_tasks, logs

    def _reduce_transitive_dependencies(
        self,
        tasks: list[PlannedTask],
    ) -> tuple[list[PlannedTask], list[str]]:
        """Remove redundant direct dependencies when an indirect path already enforces ordering.

        For each task v, if v depends on u and there is another path from u to v of length >= 2,
        the edge u -> v is transitively redundant.
        """
        logs: list[str] = []

        # Build reachability map using DFS
        # downstream: u -> list of direct dependents
        downstream: dict[str, list[str]] = defaultdict(list)
        for t in tasks:
            for dep in t.dependencies:
                downstream[dep].append(t.task_id)

        # Function to check if a path exists from start to target without using the direct edge
        def has_alternate_path(start: str, target: str) -> bool:
            visited: set[str] = set()
            queue = [v for v in downstream.get(start, []) if v != target]
            while queue:
                curr = queue.pop(0)
                if curr == target:
                    return True
                if curr not in visited:
                    visited.add(curr)
                    queue.extend(downstream.get(curr, []))
            return False

        for t in tasks:
            essential_deps: list[str] = []
            for dep in t.dependencies:
                if has_alternate_path(dep, t.task_id):
                    logs.append(
                        f"Removed redundant transitive dependency: Task '{t.task_id}' direct dependency on '{dep}' "
                        f"is already transitively enforced through an intermediate task."
                    )
                else:
                    essential_deps.append(dep)
            t.dependencies = essential_deps

        return tasks, logs
