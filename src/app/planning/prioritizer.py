"""Task prioritization subsystem analyzing critical paths and dependency impact."""

import logging

from app.planning.graph import DependencyGraph
from app.schemas.planning import TaskPriority

logger = logging.getLogger("app.planning.prioritizer")


class PrioritizationAnalysis:
    """Container holding prioritization metrics for a task."""

    def __init__(
        self,
        task_id: str,
        priority: TaskPriority,
        downstream_blocked_count: int,
        critical_path_length: int,
        rationale: str,
    ) -> None:
        self.task_id = task_id
        self.priority = priority
        self.downstream_blocked_count = downstream_blocked_count
        self.critical_path_length = critical_path_length
        self.rationale = rationale


class TaskPrioritizer:
    """Calculates explainable scheduling priorities based on DAG topology and critical path analysis."""

    def __init__(self, graph: DependencyGraph) -> None:
        self.graph = graph

    def prioritize_tasks(
        self,
        critical_keywords: list[str] | None = None,
    ) -> dict[str, TaskPriority]:
        """Compute priorities for all tasks in the dependency graph.

        Args:
            critical_keywords: Optional keywords that force elevated priority if present in task name/description.

        Returns:
            Mapping of task_id to calculated TaskPriority.
        """
        keywords = [k.lower().strip() for k in (critical_keywords or [])]
        analyses = self.analyze_all_tasks(keywords)

        priorities: dict[str, TaskPriority] = {}
        for tid, analysis in analyses.items():
            priorities[tid] = analysis.priority
            # Update the task object in-place if it exists in graph
            if tid in self.graph.tasks:
                self.graph.tasks[tid].priority = analysis.priority

        return priorities

    def analyze_all_tasks(
        self,
        critical_keywords: list[str] | None = None,
    ) -> dict[str, PrioritizationAnalysis]:
        """Analyze critical path length and downstream impact for every task."""
        keywords = [k.lower().strip() for k in (critical_keywords or [])]
        tasks = self.graph.tasks

        # Calculate reachable downstream count and longest path to a terminal node for each task
        path_lengths = self._compute_longest_paths_to_terminal()
        downstream_counts = self._compute_total_downstream_reach()

        max_path = max(path_lengths.values()) if path_lengths else 1

        results: dict[str, PrioritizationAnalysis] = {}

        for tid, task in tasks.items():
            blocked_count = downstream_counts.get(tid, 0)
            path_len = path_lengths.get(tid, 0)

            # Check explicit keyword triggers
            name_desc = f"{task.name} {task.description}".lower()
            keyword_match = any(kw in name_desc for kw in keywords)

            if keyword_match:
                prio = TaskPriority.CRITICAL
                rationale = "Explicitly elevated to CRITICAL due to user constraint keyword match."
            elif path_len == max_path and blocked_count >= 2:
                prio = TaskPriority.CRITICAL
                rationale = (
                    f"CRITICAL: Lies on the global critical path (length {path_len}) "
                    f"and blocks {blocked_count} downstream tasks."
                )
            elif path_len >= 2 or blocked_count >= 2:
                prio = TaskPriority.HIGH
                rationale = f"HIGH: Critical path length {path_len}, blocking {blocked_count} downstream tasks."
            elif blocked_count == 1:
                prio = TaskPriority.MEDIUM
                rationale = "MEDIUM: Blocks 1 downstream task."
            else:
                prio = TaskPriority.LOW
                rationale = "LOW: Terminal or leaf task with zero downstream dependents."

            results[tid] = PrioritizationAnalysis(
                task_id=tid,
                priority=prio,
                downstream_blocked_count=blocked_count,
                critical_path_length=path_len,
                rationale=rationale,
            )

        return results

    def _compute_longest_paths_to_terminal(self) -> dict[str, int]:
        """Compute the longest path from each task to any leaf/terminal task."""
        topo_order = self.graph.topological_sort()
        # Process in reverse topological order (dynamic programming)
        longest: dict[str, int] = dict.fromkeys(topo_order, 0)

        for u in reversed(topo_order):
            dependents = self.graph.get_direct_dependents(u)
            if dependents:
                longest[u] = 1 + max(longest[v] for v in dependents)
            else:
                longest[u] = 0

        return longest

    def _compute_total_downstream_reach(self) -> dict[str, int]:
        """Count the total number of transitive downstream tasks reachable from each task."""
        topo_order = self.graph.topological_sort()
        reach: dict[str, set[str]] = {tid: set() for tid in topo_order}

        for u in reversed(topo_order):
            for v in self.graph.get_direct_dependents(u):
                reach[u].add(v)
                reach[u].update(reach[v])

        return {tid: len(sub_set) for tid, sub_set in reach.items()}
