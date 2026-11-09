"""Dependency graph (DAG) engine for validating task structures, detecting cycles, and topological sorting."""

import logging
from collections import defaultdict, deque

from app.schemas.planning import PlannedTask

logger = logging.getLogger("app.planning.graph")


class DependencyCycleError(ValueError):
    """Raised when a circular dependency is detected in the task dependency graph."""

    def __init__(self, cycle_path: list[str]) -> None:
        self.cycle_path = cycle_path
        path_str = " -> ".join(cycle_path)
        super().__init__(f"Circular dependency detected in execution plan: {path_str}")


class MissingDependencyError(ValueError):
    """Raised when a task references a dependency that does not exist in the plan."""

    def __init__(self, task_id: str, missing_dependency: str) -> None:
        self.task_id = task_id
        self.missing_dependency = missing_dependency
        super().__init__(f"Task '{task_id}' depends on non-existent task '{missing_dependency}'.")


class SelfDependencyError(ValueError):
    """Raised when a task lists itself as a dependency."""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"Task '{task_id}' lists itself as a dependency.")


class DependencyGraph:
    """Directed Acyclic Graph (DAG) representation for planned task scheduling."""

    def __init__(self, tasks: list[PlannedTask] | None = None) -> None:
        self._tasks: dict[str, PlannedTask] = {}
        # adjacency: u -> list of v (where v depends on u, meaning u must complete before v starts)
        self._downstream: dict[str, list[str]] = defaultdict(list)
        # upstream: v -> set of u (where v depends on u)
        self._upstream: dict[str, set[str]] = defaultdict(set)

        if tasks:
            for task in tasks:
                self.add_task(task)

    @property
    def tasks(self) -> dict[str, PlannedTask]:
        """Return mapping of task_id to PlannedTask."""
        return self._tasks

    def add_task(self, task: PlannedTask) -> None:
        """Add a planned task to the dependency graph."""
        task_id = task.task_id
        if task_id in task.dependencies:
            raise SelfDependencyError(task_id)

        self._tasks[task_id] = task

        # Ensure keys exist in adjacency maps
        if task_id not in self._downstream:
            self._downstream[task_id] = []
        if task_id not in self._upstream:
            self._upstream[task_id] = set()

        for dep in task.dependencies:
            self._upstream[task_id].add(dep)
            self._downstream[dep].append(task_id)

    def validate_graph(self) -> list[str]:
        """Validate structural integrity: missing dependencies, self-dependencies, and cycles.

        Returns:
            List of validation error strings (empty if graph is completely valid).
        """
        errors: list[str] = []

        # 1. Check for missing dependencies and self-dependencies
        for task_id, task in self._tasks.items():
            for dep in task.dependencies:
                if dep == task_id:
                    errors.append(f"Task '{task_id}' cannot depend on itself.")
                elif dep not in self._tasks:
                    errors.append(f"Task '{task_id}' references unknown dependency '{dep}'.")

        # 2. Check for cycles if references are valid
        if not errors:
            cycle = self.detect_cycle()
            if cycle:
                path_str = " -> ".join(cycle)
                errors.append(f"Circular dependency detected: {path_str}")

        return errors

    def detect_cycle(self) -> list[str] | None:
        """Detect any circular dependency cycle using 3-color DFS.

        Returns:
            List of task_ids representing the cycle path (e.g. ['A', 'B', 'C', 'A']), or None.
        """
        # States: 0 = unvisited (white), 1 = visiting (gray), 2 = visited (black)
        state: dict[str, int] = dict.fromkeys(self._tasks, 0)
        parent: dict[str, str | None] = dict.fromkeys(self._tasks)
        cycle_path: list[str] = []

        def dfs(u: str) -> bool:
            state[u] = 1
            for v in self._downstream.get(u, []):
                if v not in state:
                    continue
                if state[v] == 1:
                    # Found a cycle from v to u back to v
                    cur = u
                    cycle = [v, cur]
                    while cur != v and parent[cur] is not None:
                        cur = parent[cur]  # type: ignore[assignment]
                        cycle.append(cur)
                    cycle.reverse()
                    cycle_path.extend(cycle)
                    return True
                if state[v] == 0:
                    parent[v] = u
                    if dfs(v):
                        return True
            state[u] = 2
            return False

        for node in list(self._tasks.keys()):
            if state[node] == 0 and dfs(node):
                return cycle_path
        return None

    def get_root_tasks(self) -> list[PlannedTask]:
        """Retrieve tasks with no dependencies (in-degree 0)."""
        roots: list[PlannedTask] = []
        for tid, task in self._tasks.items():
            if not self._upstream[tid]:
                roots.append(task)
        return roots

    def get_terminal_tasks(self) -> list[PlannedTask]:
        """Retrieve tasks with no dependents (out-degree 0)."""
        terminals: list[PlannedTask] = []
        for tid, task in self._tasks.items():
            if not self._downstream[tid]:
                terminals.append(task)
        return terminals

    def topological_sort(self) -> list[str]:
        """Compute a valid sequential execution order using Kahn's algorithm.

        Returns:
            List of task_ids in topologically sorted order.

        Raises:
            DependencyCycleError: If the graph contains a circular dependency.
            MissingDependencyError: If a task depends on an absent task.
        """
        # Validate dependency references first
        for tid, task in self._tasks.items():
            for dep in task.dependencies:
                if dep not in self._tasks:
                    raise MissingDependencyError(tid, dep)

        in_degree: dict[str, int] = {tid: len(self._upstream[tid]) for tid in self._tasks}
        queue: deque[str] = deque([tid for tid, deg in in_degree.items() if deg == 0])
        sorted_order: list[str] = []

        while queue:
            u = queue.popleft()
            sorted_order.append(u)

            for v in self._downstream.get(u, []):
                in_degree[v] -= 1
                if in_degree[v] == 0:
                    queue.append(v)

        if len(sorted_order) != len(self._tasks):
            cycle = self.detect_cycle() or ["unknown cycle"]
            raise DependencyCycleError(cycle)

        return sorted_order

    def get_parallel_levels(self) -> list[list[str]]:
        """Group task_ids into concurrent execution levels based on dependency depth.

        Level 0 contains root tasks.
        Level k contains tasks whose dependencies are all in levels < k.

        Returns:
            List of levels, where each level is a list of independent task_ids.
        """
        errors = self.validate_graph()
        if errors:
            raise ValueError(
                f"Cannot generate parallel levels for invalid graph: {'; '.join(errors)}"
            )

        in_degree: dict[str, int] = {tid: len(self._upstream[tid]) for tid in self._tasks}
        current_level = [tid for tid, deg in in_degree.items() if deg == 0]
        levels: list[list[str]] = []

        while current_level:
            levels.append(sorted(current_level))
            next_level: list[str] = []
            for u in current_level:
                for v in self._downstream.get(u, []):
                    in_degree[v] -= 1
                    if in_degree[v] == 0:
                        next_level.append(v)
            current_level = next_level

        return levels

    def get_direct_dependencies(self, task_id: str) -> list[str]:
        """Return list of task_ids that task_id directly depends on."""
        return sorted(self._upstream.get(task_id, set()))

    def get_direct_dependents(self, task_id: str) -> list[str]:
        """Return list of task_ids that directly depend on task_id."""
        return sorted(self._downstream.get(task_id, []))
