"""Centralized PlanningService orchestrating task decomposition, validation, optimization, and persistence."""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.registry import AgentRegistry, get_agent_registry
from app.db.models.execution_plan import ExecutionPlanModel, PlanningIterationModel
from app.planning.decomposer import TaskDecomposer
from app.planning.graph import DependencyGraph
from app.planning.matcher import AgentCapabilityMatcher
from app.planning.optimizer import PlanOptimizer
from app.planning.prioritizer import TaskPrioritizer
from app.planning.stages import StageGenerator
from app.planning.validator import PlanValidator
from app.schemas.planning import (
    ExecutionPlan,
    ExecutionStage,
    PlannedTask,
    PlanningRequest,
    PlanStatus,
)
from app.services.agent_execution_service import (
    AgentExecutionService,
    get_agent_execution_service,
)

logger = logging.getLogger("app.services.planning_service")


class PlanningService:
    """Orchestrates dynamic planning, validation, optimization, and persistence."""

    def __init__(
        self,
        decomposer: TaskDecomposer | None = None,
        validator: PlanValidator | None = None,
        optimizer: PlanOptimizer | None = None,
        matcher: AgentCapabilityMatcher | None = None,
        registry: AgentRegistry | None = None,
    ) -> None:
        self.registry = registry or get_agent_registry()
        self.matcher = matcher or AgentCapabilityMatcher(self.registry)
        self.decomposer = decomposer or TaskDecomposer(
            matcher=self.matcher,
            registry=self.registry,
        )
        self.validator = validator or PlanValidator(self.registry)
        self.optimizer = optimizer or PlanOptimizer()

    async def generate_plan(
        self,
        request: PlanningRequest,
        session: AsyncSession | None = None,
    ) -> ExecutionPlan:
        """Generate, validate, optimize, and persist an execution plan for a user request.

        Args:
            request: The client planning request.
            session: Optional database session for persisting the plan.

        Returns:
            Fully validated and optimized ExecutionPlan.
        """
        start_time = datetime.now(UTC)
        logger.info("Generating execution plan for request: '%s'", request.user_request[:80])

        current_errors: list[str] = []
        best_plan: ExecutionPlan | None = None
        iterations_log: list[dict[str, Any]] = []

        max_iterations = min(max(request.max_planning_iterations, 1), 10)

        for iteration in range(1, max_iterations + 1):
            logger.info("Executing planning iteration %d of %d", iteration, max_iterations)

            # 1. Run task decomposition
            plan = await self.decomposer.decompose_request(
                request=request,
                iteration=iteration,
                prior_validation_errors=current_errors if iteration > 1 else None,
            )

            # 2. Build dependency graph, calculate priorities and stages
            try:
                graph = DependencyGraph(plan.tasks)
                prioritizer = TaskPrioritizer(graph)
                prioritizer.prioritize_tasks(critical_keywords=request.constraints)

                stage_gen = StageGenerator(graph)
                plan.execution_stages = stage_gen.generate_stages()
            except Exception as e:
                logger.warning(
                    "Stage / graph generation encountered issue in iteration %d: %s", iteration, e
                )
                current_errors = [str(e)]
                iterations_log.append(
                    {
                        "iteration": iteration,
                        "validation_passed": False,
                        "errors": current_errors,
                        "raw_plan": plan.model_dump(mode="json"),
                    }
                )
                best_plan = plan
                continue

            # 3. Validate plan integrity
            val_result = self.validator.validate_plan(plan, request)
            iterations_log.append(
                {
                    "iteration": iteration,
                    "validation_passed": val_result.is_valid,
                    "errors": val_result.errors,
                    "raw_plan": plan.model_dump(mode="json"),
                }
            )

            if val_result.is_valid:
                plan.plan_status = PlanStatus.VALIDATED
                best_plan = plan
                logger.info("Plan validation PASSED on iteration %d", iteration)
                break
            else:
                current_errors = val_result.errors
                plan.validation_errors = current_errors
                best_plan = plan
                logger.warning(
                    "Plan validation failed on iteration %d (%d errors): %s",
                    iteration,
                    len(current_errors),
                    "; ".join(current_errors),
                )

        if best_plan is None:
            raise ValueError("Failed to produce an execution plan.")

        # 4. Conservative Optimization
        if best_plan.plan_status == PlanStatus.VALIDATED:
            optimized_plan, opt_logs = self.optimizer.optimize_plan(best_plan)
            optimized_plan.plan_status = PlanStatus.OPTIMIZED
            best_plan = optimized_plan
            logger.info("Plan optimization complete (%d actions recorded)", len(opt_logs))

        # Record total planning duration in metadata
        elapsed_ms = int((datetime.now(UTC) - start_time).total_seconds() * 1000)
        best_plan.metadata["total_planning_duration_ms"] = elapsed_ms
        best_plan.metadata["iterations_count"] = len(iterations_log)

        # 5. Database Persistence (if database session is available)
        if session is not None:
            await self._persist_plan(best_plan, iterations_log, session)

        return best_plan

    async def _persist_plan(
        self,
        plan: ExecutionPlan,
        iterations_log: list[dict[str, Any]],
        session: AsyncSession,
    ) -> None:
        """Persist the approved plan and its iteration history in MySQL."""
        try:
            workflow_uuid = uuid.UUID(plan.workflow_id) if plan.workflow_id else None
            plan_uuid = uuid.UUID(plan.plan_id)

            plan_model = ExecutionPlanModel(
                id=plan_uuid,
                workflow_id=workflow_uuid,
                objective=plan.objective,
                plan_status=plan.plan_status.value,
                task_count=len(plan.tasks),
                tasks_data=[t.model_dump(mode="json") for t in plan.tasks],
                stages_data=[s.model_dump(mode="json") for s in plan.execution_stages],
                plan_metadata=plan.metadata,
                validation_errors=plan.validation_errors,
                version=1,
            )
            session.add(plan_model)
            await session.flush()

            for item in iterations_log:
                iter_model = PlanningIterationModel(
                    id=uuid.uuid4(),
                    plan_id=plan_uuid,
                    iteration_number=item["iteration"],
                    prompt=None,
                    raw_plan_data=item["raw_plan"],
                    validation_passed=item["validation_passed"],
                    validation_errors=item["errors"],
                )
                session.add(iter_model)

            await session.flush()
            logger.info(
                "Persisted ExecutionPlan '%s' with %d iterations", plan.plan_id, len(iterations_log)
            )
        except Exception as e:
            logger.error("Failed to persist ExecutionPlan '%s' to database: %s", plan.plan_id, e)

    async def get_plan_by_id(
        self,
        plan_id: uuid.UUID,
        session: AsyncSession,
    ) -> ExecutionPlan | None:
        """Retrieve a persisted ExecutionPlan by its primary key UUID."""
        query = select(ExecutionPlanModel).where(ExecutionPlanModel.id == plan_id)
        result = await session.execute(query)
        model = result.scalar_one_or_none()
        if not model:
            return None

        tasks = [PlannedTask(**t) for t in (model.tasks_data or [])]
        stages = [ExecutionStage(**s) for s in (model.stages_data or [])]

        return ExecutionPlan(
            plan_id=str(model.id),
            workflow_id=str(model.workflow_id) if model.workflow_id else None,
            objective=model.objective,
            tasks=tasks,
            execution_stages=stages,
            estimated_task_count=model.task_count,
            plan_status=PlanStatus(model.plan_status),
            validation_errors=model.validation_errors or [],
            metadata=model.plan_metadata or {},
            created_at=model.created_at,
        )

    async def get_plan_tasks(
        self,
        plan_id: uuid.UUID,
        session: AsyncSession,
    ) -> list[PlannedTask] | None:
        """Retrieve planned tasks for a given plan."""
        plan = await self.get_plan_by_id(plan_id, session)
        return plan.tasks if plan else None

    async def get_plan_stages(
        self,
        plan_id: uuid.UUID,
        session: AsyncSession,
    ) -> list[ExecutionStage] | None:
        """Retrieve execution stages for a given plan."""
        plan = await self.get_plan_by_id(plan_id, session)
        return plan.execution_stages if plan else None


# FastAPI Dependency Provider
_default_planning_service: PlanningService | None = None


def get_planning_service(
    execution_service: AgentExecutionService = Depends(get_agent_execution_service),
) -> PlanningService:
    """FastAPI dependency injecting the centralized PlanningService."""
    global _default_planning_service
    if _default_planning_service is None:
        matcher = AgentCapabilityMatcher()
        decomposer = TaskDecomposer(execution_service=execution_service, matcher=matcher)
        validator = PlanValidator()
        optimizer = PlanOptimizer()
        _default_planning_service = PlanningService(
            decomposer=decomposer,
            validator=validator,
            optimizer=optimizer,
            matcher=matcher,
        )
    return _default_planning_service
