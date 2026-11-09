"""Centralized workflow execution service invoking LangGraph and synchronizing persistence."""

import logging
import uuid
from typing import Any

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.task import TaskStatus
from app.db.models.workflow import WorkflowStatus
from app.orchestration.checkpointer import (
    WorkflowCheckpointAdapter,
    get_checkpoint_adapter,
)
from app.orchestration.graph import build_workflow_graph
from app.orchestration.nodes import OrchestrationNodes
from app.orchestration.state import NexusState, create_initial_state
from app.services.agent_execution_service import (
    AgentExecutionService,
    get_agent_execution_service,
)
from app.services.workflow_service import (
    create_task,
    create_workflow,
    get_workflow_by_id,
    update_task_status,
    update_workflow_status,
)

logger = logging.getLogger("app.orchestration.executor")


class WorkflowExecutor:
    """Coordinates execution of multi-agent LangGraph workflows with database synchronization."""

    def __init__(
        self,
        execution_service: AgentExecutionService | None = None,
        checkpoint_adapter: WorkflowCheckpointAdapter | None = None,
    ) -> None:
        self.execution_service = execution_service or get_agent_execution_service()
        self.checkpoint_adapter = checkpoint_adapter or get_checkpoint_adapter()
        self.nodes = OrchestrationNodes(execution_service=self.execution_service)
        self.graph = build_workflow_graph(
            nodes=self.nodes,
            checkpointer=self.checkpoint_adapter.checkpointer,
        )

    async def execute_workflow(
        self,
        user_request: str,
        workflow_id: uuid.UUID | None = None,
        task_description: str | None = None,
        max_revisions: int = 3,
        recursion_limit: int = 50,
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        """Execute a stateful multi-agent workflow from start to completion.

        Args:
            user_request: High-level user prompt or objective.
            workflow_id: Existing workflow UUID, or None to generate and persist.
            task_description: Optional detailed task breakdown.
            max_revisions: Limit on reviewer revision iterations.
            recursion_limit: Maximum graph steps allowed.
            session: Optional AsyncSession for database synchronization.

        Returns:
            Dictionary containing final workflow outcome, status, and state details.
        """
        # 1. Resolve or create Workflow in Database
        if session is not None:
            if workflow_id is not None:
                workflow_obj = await get_workflow_by_id(session, workflow_id)
                if workflow_obj is None:
                    workflow_obj = await create_workflow(
                        session=session,
                        name=f"Workflow-{str(workflow_id)[:8]}",
                        description=user_request,
                        input_data={"user_request": user_request, "max_revisions": max_revisions},
                    )
            else:
                workflow_obj = await create_workflow(
                    session=session,
                    name=f"Workflow-{user_request[:30]}",
                    description=user_request,
                    input_data={"user_request": user_request, "max_revisions": max_revisions},
                )
            active_workflow_id = workflow_obj.id
            await update_workflow_status(session, active_workflow_id, WorkflowStatus.RUNNING)
        else:
            active_workflow_id = workflow_id or uuid.uuid4()

        workflow_id_str = str(active_workflow_id)
        logger.info("[Workflow %s] Starting execution: '%s'", workflow_id_str, user_request[:60])

        # 2. Build initial state and execution config
        initial_state = create_initial_state(
            workflow_id=workflow_id_str,
            user_request=user_request,
            task_description=task_description,
            max_revisions=max_revisions,
        )

        config = self.checkpoint_adapter.build_config(workflow_id_str)
        config["recursion_limit"] = recursion_limit

        # 3. Execute LangGraph StateGraph
        try:
            final_state: NexusState = await self.graph.ainvoke(initial_state, config=config)

            is_success = (
                final_state.get("execution_status") == "COMPLETED"
                and final_state.get("final_output") is not None
            )
            terminal_status = WorkflowStatus.COMPLETED if is_success else WorkflowStatus.FAILED

            result_payload = {
                "workflow_id": workflow_id_str,
                "status": terminal_status.value,
                "final_output": final_state.get("final_output"),
                "selected_agents": final_state.get("selected_agents", []),
                "completed_agents": final_state.get("completed_agents", []),
                "revision_count": final_state.get("revision_count", 0),
                "review_result": final_state.get("review_result"),
                "agent_results": final_state.get("agent_results", {}),
                "errors": final_state.get("errors", []),
            }

            # 4. Synchronize status and deliverables to MySQL
            if session is not None:
                await update_workflow_status(
                    session=session,
                    workflow_id=active_workflow_id,
                    status=terminal_status,
                    result_data=result_payload,
                )

                # Persist subtasks for each completed agent deliverable
                for agent_name, agent_res in final_state.get("agent_results", {}).items():
                    subtask = await create_task(
                        session=session,
                        workflow_id=active_workflow_id,
                        name=f"Agent Execution: {agent_name.capitalize()}",
                        task_type=agent_name,
                        description=f"Generated deliverables for agent '{agent_name}'",
                        input_data={"agent": agent_name},
                    )
                    task_status = (
                        TaskStatus.FAILED if "error" in agent_res else TaskStatus.COMPLETED
                    )
                    await update_task_status(
                        session=session,
                        task_id=subtask.id,
                        status=task_status,
                        result_data=agent_res
                        if isinstance(agent_res, dict)
                        else {"content": agent_res},
                    )

            logger.info(
                "[Workflow %s] Completed with status=%s (revisions=%d)",
                workflow_id_str,
                terminal_status.value,
                final_state.get("revision_count", 0),
            )
            return result_payload

        except Exception as exc:
            logger.exception(
                "[Workflow %s] Execution encountered unhandled failure: %s",
                workflow_id_str,
                str(exc),
            )
            error_payload = {
                "workflow_id": workflow_id_str,
                "status": WorkflowStatus.FAILED.value,
                "error": str(exc),
                "final_output": None,
            }
            if session is not None:
                await update_workflow_status(
                    session=session,
                    workflow_id=active_workflow_id,
                    status=WorkflowStatus.FAILED,
                    result_data=error_payload,
                )
            return error_payload


_default_executor: WorkflowExecutor | None = None


def get_workflow_executor(
    execution_service: AgentExecutionService = Depends(get_agent_execution_service),
    checkpoint_adapter: WorkflowCheckpointAdapter = Depends(get_checkpoint_adapter),
) -> WorkflowExecutor:
    """FastAPI dependency providing the WorkflowExecutor singleton."""
    global _default_executor
    if not isinstance(execution_service, AgentExecutionService):
        execution_service = get_agent_execution_service()
    if not isinstance(checkpoint_adapter, WorkflowCheckpointAdapter):
        checkpoint_adapter = get_checkpoint_adapter()
    if _default_executor is None:
        _default_executor = WorkflowExecutor(
            execution_service=execution_service,
            checkpoint_adapter=checkpoint_adapter,
        )
    return _default_executor
