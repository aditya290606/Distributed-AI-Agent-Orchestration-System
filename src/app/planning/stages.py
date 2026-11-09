"""Execution stage generator transforming dependency levels into scheduled execution phases."""

import logging

from app.planning.graph import DependencyGraph
from app.schemas.planning import ExecutionStage

logger = logging.getLogger("app.planning.stages")


class StageGenerator:
    """Partitions DAG tasks into sequential execution stages respecting dependencies and concurrency limits."""

    def __init__(self, graph: DependencyGraph) -> None:
        self.graph = graph

    def generate_stages(
        self,
        max_concurrency: int = 5,
    ) -> list[ExecutionStage]:
        """Convert the dependency graph into ordered ExecutionStages.

        Args:
            max_concurrency: Maximum number of concurrent tasks per stage.
                If a topological level exceeds this, it is partitioned into sub-stages.

        Returns:
            List of ExecutionStage objects.
        """
        levels = self.graph.get_parallel_levels()
        tasks = self.graph.tasks
        stages: list[ExecutionStage] = []
        stage_num = 1

        completed_task_ids: set[str] = set()

        for _level_idx, level_task_ids in enumerate(levels, start=1):
            level_tasks = [tasks[tid] for tid in level_task_ids]

            # Verify that all dependencies for all tasks in this level have been satisfied in prior stages
            for t in level_tasks:
                unsatisfied = [dep for dep in t.dependencies if dep not in completed_task_ids]
                if unsatisfied:
                    raise ValueError(
                        f"Stage dependency invariant violated for task '{t.task_id}': "
                        f"dependencies {unsatisfied} are not completed in earlier stages."
                    )

            # Partition level if it exceeds max_concurrency
            chunks = [
                level_tasks[i : i + max_concurrency]
                for i in range(0, len(level_tasks), max_concurrency)
            ]

            for chunk_idx, chunk in enumerate(chunks):
                # Generate descriptive title based on task types
                task_types = list({t.task_type.capitalize() for t in chunk})
                descriptor = " & ".join(task_types[:2]) or "Execution"

                sub_suffix = f" (Part {chunk_idx + 1})" if len(chunks) > 1 else ""
                stage_name = f"Stage {stage_num}: {descriptor}{sub_suffix}"

                stage = ExecutionStage(
                    stage_number=stage_num,
                    name=stage_name,
                    tasks=chunk,
                    can_run_parallel=len(chunk) > 1,
                )
                stages.append(stage)

                for t in chunk:
                    completed_task_ids.add(t.task_id)

                stage_num += 1

        return stages
