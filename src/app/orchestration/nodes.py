"""LangGraph node implementations invoking specialized agents through AgentExecutionService."""

import logging
from typing import Any

from app.agents.contracts import AgentExecutionStatus, AgentInput
from app.orchestration.state import NexusState
from app.services.agent_execution_service import (
    AgentExecutionService,
    get_agent_execution_service,
)

logger = logging.getLogger("app.orchestration.nodes")


class OrchestrationNodes:
    """Encapsulates node invocation logic for each specialized agent in the workflow graph."""

    def __init__(self, execution_service: AgentExecutionService | None = None) -> None:
        self.execution_service = execution_service or get_agent_execution_service()

    async def _execute_agent(
        self,
        agent_id: str,
        task_description: str,
        context: dict[str, Any],
        instructions: str | None,
        state: NexusState,
    ) -> dict[str, Any]:
        """Shared execution helper invoking an agent and constructing state update dictionary."""
        agent_input = AgentInput(
            workflow_id=state.get("workflow_id"),
            task_id=state.get("task_id"),
            task_description=task_description,
            context=context,
            instructions=instructions,
        )

        output = await self.execution_service.execute_agent(
            agent_id=agent_id,
            input_data=agent_input,
        )

        if output.status == AgentExecutionStatus.FAILED:
            error_msg = output.error or f"Agent '{agent_id}' execution failed without error trace."
            logger.error(
                "Node '%s' failed for workflow %s: %s", agent_id, state["workflow_id"], error_msg
            )
            return {
                "current_agent": agent_id,
                "completed_agents": [agent_id],
                "errors": [f"[{agent_id}] {error_msg}"],
                "execution_metadata": {agent_id: output.execution_metadata},
                "agent_results": {agent_id: {"error": error_msg, "status": "FAILED"}},
            }

        return {
            "current_agent": agent_id,
            "completed_agents": [agent_id],
            "agent_results": {agent_id: output.result},
            "execution_metadata": {agent_id: output.execution_metadata},
        }

    async def supervisor_node(self, state: NexusState) -> dict[str, Any]:
        """Supervisor node classifying task complexity and recommending agent delegation."""
        logger.info("[Workflow %s] Executing Supervisor Node", state["workflow_id"])
        context = {
            "user_request": state["user_request"],
            "existing_results": state.get("agent_results") or {},
        }
        updates = await self._execute_agent(
            agent_id="supervisor",
            task_description=state["task_description"],
            context=context,
            instructions=(
                "Assess whether this is a simple single-domain task or complex multi-step workflow. "
                "Select suitable specialized agents."
            ),
            state=state,
        )

        result = updates.get("agent_results", {}).get("supervisor", {})
        if "error" in result:
            updates["execution_status"] = "FAILED"
            return updates

        recommended = result.get("recommended_agents", [])
        updates["selected_agents"] = recommended
        updates["execution_status"] = "SUPERVISED"

        # If simple task and 1 specialist recommended, set pending agents directly; else route to planner
        if len(recommended) == 1 and recommended[0] != "planner":
            updates["pending_agents"] = recommended
        else:
            updates["pending_agents"] = ["planner"]

        return updates

    async def planner_node(self, state: NexusState) -> dict[str, Any]:
        """Planner node decomposing complex objectives into ordered execution steps."""
        logger.info("[Workflow %s] Executing Planner Node", state["workflow_id"])
        supervisor_res = (state.get("agent_results") or {}).get("supervisor") or {}
        context = {
            "user_request": state["user_request"],
            "supervisor_recommendations": supervisor_res,
        }

        prior_errors = (state.get("metadata") or {}).get("planning_validation_errors", [])
        instructions = "Decompose the objective into discrete, dependency-ordered subtasks with assigned agents."
        if prior_errors:
            error_bullets = "\n".join(f"- {e}" for e in prior_errors)
            instructions += f"\n\nIMPORTANT: Prior plan validation failed. Fix the following defects:\n{error_bullets}"

        planning_iteration = (state.get("metadata") or {}).get("planning_iteration", 0) + 1

        updates = await self._execute_agent(
            agent_id="planner",
            task_description=state["task_description"],
            context=context,
            instructions=instructions,
            state=state,
        )

        result = updates.get("agent_results", {}).get("planner", {})
        if "error" in result:
            updates["execution_status"] = "FAILED"
            return updates

        steps = result.get("steps", [])
        assigned_agents = [s["assigned_agent"] for s in steps if "assigned_agent" in s]
        updates["execution_plan"] = result
        updates["pending_agents"] = assigned_agents if assigned_agents else ["researcher"]
        updates["execution_status"] = "PLANNED"
        updates["metadata"] = {
            "planning_iteration": planning_iteration,
            "planning_validation_errors": [],
        }
        return updates

    async def plan_validation_node(self, state: NexusState) -> dict[str, Any]:
        """Plan validation node verifying structural, dependency, and capability rules."""
        logger.info("[Workflow %s] Executing Plan Validation Node", state["workflow_id"])
        raw_plan = state.get("execution_plan")

        if not raw_plan or not isinstance(raw_plan, dict):
            logger.warning(
                "[Workflow %s] No execution plan found to validate", state["workflow_id"]
            )
            return {
                "execution_status": "PLAN_INVALID",
                "metadata": {
                    "planning_validation_errors": ["Execution plan is empty or missing."],
                },
            }

        steps = raw_plan.get("steps", [])
        if not steps:
            logger.warning("[Workflow %s] Plan contains no steps", state["workflow_id"])
            return {
                "execution_status": "PLAN_INVALID",
                "metadata": {
                    "planning_validation_errors": ["Execution plan contains no planned steps."],
                },
            }

        # Validate step integrity
        validation_errors: list[str] = []
        step_numbers = set()
        for idx, s in enumerate(steps, start=1):
            s_num = s.get("step_number", idx)
            if s_num in step_numbers:
                validation_errors.append(f"Duplicate step number {s_num} in plan.")
            step_numbers.add(s_num)

        for idx, s in enumerate(steps, start=1):
            s_num = s.get("step_number", idx)
            deps = s.get("dependencies", [])
            for dep in deps:
                if dep == s_num:
                    validation_errors.append(f"Step {s_num} lists itself as a dependency.")
                elif isinstance(dep, int) and dep not in step_numbers:
                    validation_errors.append(f"Step {s_num} references non-existent step {dep}.")

        # Check for cycles
        if not validation_errors:
            downstream: dict[int, list[int]] = {n: [] for n in step_numbers}
            for idx, s in enumerate(steps, start=1):
                s_num = s.get("step_number", idx)
                for dep in s.get("dependencies", []):
                    if isinstance(dep, int) and dep in downstream:
                        downstream[dep].append(s_num)

            # Cycle detection via DFS
            visited: dict[int, int] = dict.fromkeys(step_numbers, 0)

            def has_cycle(u: int) -> bool:
                visited[u] = 1
                for v in downstream.get(u, []):
                    if visited[v] == 1:
                        return True
                    if visited[v] == 0 and has_cycle(v):
                        return True
                visited[u] = 2
                return False

            for n in step_numbers:
                if visited[n] == 0 and has_cycle(n):
                    validation_errors.append("Circular dependency detected in plan steps.")
                    break

        iteration = (state.get("metadata") or {}).get("planning_iteration", 1)
        max_iterations = state.get("metadata", {}).get("max_planning_iterations", 3)

        if validation_errors:
            logger.warning(
                "[Workflow %s] Plan validation failed with %d errors: %s",
                state["workflow_id"],
                len(validation_errors),
                "; ".join(validation_errors),
            )
            if iteration < max_iterations:
                return {
                    "execution_status": "PLAN_INVALID",
                    "metadata": {
                        "planning_validation_errors": validation_errors,
                        "planning_iteration": iteration,
                    },
                }
            else:
                logger.error(
                    "[Workflow %s] Maximum planning repair iterations (%d) reached.",
                    state["workflow_id"],
                    max_iterations,
                )
                return {
                    "execution_status": "PLAN_REPAIR_EXHAUSTED",
                    "errors": [
                        f"Plan validation failed after {max_iterations} attempts: {'; '.join(validation_errors)}"
                    ],
                    "metadata": {
                        "planning_validation_errors": validation_errors,
                        "planning_iteration": iteration,
                    },
                }

        logger.info("[Workflow %s] Plan validation PASSED", state["workflow_id"])
        assigned_agents = [s["assigned_agent"] for s in steps if "assigned_agent" in s]
        return {
            "execution_status": "PLAN_VALIDATED",
            "pending_agents": assigned_agents if assigned_agents else ["researcher"],
            "metadata": {
                "planning_validation_errors": [],
                "planning_iteration": iteration,
            },
        }

    async def research_node(self, state: NexusState) -> dict[str, Any]:
        """Research node synthesizing factual domain knowledge."""
        logger.info("[Workflow %s] Executing Research Node", state["workflow_id"])
        context = {
            "user_request": state["user_request"],
            "planner_notes": state.get("execution_plan"),
            "existing_results": state.get("agent_results") or {},
        }
        updates = await self._execute_agent(
            agent_id="researcher",
            task_description=f"Synthesize research for: {state['task_description']}",
            context=context,
            instructions="Provide factual findings, clarify assumptions, and highlight information gaps.",
            state=state,
        )
        # Update pending agents by removing researcher
        pending = [a for a in state.get("pending_agents", []) if a != "researcher"]
        updates["pending_agents"] = pending
        updates["execution_status"] = "RESEARCHED"
        return updates

    async def analysis_node(self, state: NexusState) -> dict[str, Any]:
        """Analysis node performing structured data evaluation and alternative comparisons."""
        logger.info("[Workflow %s] Executing Analysis Node", state["workflow_id"])
        context = {
            "user_request": state["user_request"],
            "research_findings": (state.get("agent_results") or {}).get("researcher"),
            "existing_results": state.get("agent_results") or {},
        }
        updates = await self._execute_agent(
            agent_id="analyst",
            task_description=f"Analyze findings for: {state['task_description']}",
            context=context,
            instructions="Evaluate alternatives, detect trends, and formulate comparative conclusions.",
            state=state,
        )
        pending = [a for a in state.get("pending_agents", []) if a != "analyst"]
        updates["pending_agents"] = pending
        updates["execution_status"] = "ANALYZED"
        return updates

    async def coding_node(self, state: NexusState) -> dict[str, Any]:
        """Coding node generating code deliverables and architectural specifications."""
        logger.info("[Workflow %s] Executing Coding Node", state["workflow_id"])
        review_feedback = state.get("review_result") or {}
        context = {
            "user_request": state["user_request"],
            "plan": state.get("execution_plan"),
            "research": (state.get("agent_results") or {}).get("researcher"),
            "analysis": (state.get("agent_results") or {}).get("analyst"),
            "reviewer_feedback": review_feedback
            if review_feedback.get("verdict") == "REVISE"
            else None,
        }
        updates = await self._execute_agent(
            agent_id="coder",
            task_description=f"Implement software solution for: {state['task_description']}",
            context=context,
            instructions=(
                "Generate clean code blocks, document edge cases, and provide architecture notes. "
                "Address any reviewer feedback if present."
            ),
            state=state,
        )
        pending = [a for a in state.get("pending_agents", []) if a != "coder"]
        updates["pending_agents"] = pending
        updates["execution_status"] = "CODED"
        return updates

    async def reviewer_node(self, state: NexusState) -> dict[str, Any]:
        """Reviewer node auditing generated deliverables and issuing acceptance or revision verdicts."""
        logger.info(
            "[Workflow %s] Executing Reviewer Node (Revision: %d)",
            state["workflow_id"],
            state["revision_count"],
        )
        deliverables = {
            k: v
            for k, v in (state.get("agent_results") or {}).items()
            if k in ("coder", "analyst", "researcher", "planner")
        }
        context = {
            "user_request": state["user_request"],
            "deliverables_under_review": deliverables,
            "revision_count": state["revision_count"],
        }
        updates = await self._execute_agent(
            agent_id="reviewer",
            task_description=f"Audit deliverables for: {state['task_description']}",
            context=context,
            instructions="Verify completeness, consistency, and correctness. Render an ACCEPT, REVISE, or REJECT verdict.",
            state=state,
        )

        review_res = updates.get("agent_results", {}).get("reviewer", {})
        updates["review_result"] = review_res
        updates["execution_status"] = "REVIEWED"

        if review_res.get("verdict") == "REVISE":
            updates["revision_count"] = state["revision_count"] + 1

        return updates

    async def report_node(self, state: NexusState) -> dict[str, Any]:
        """Report node synthesizing all agent deliverables into an executive markdown document."""
        logger.info("[Workflow %s] Executing Report Generation Node", state["workflow_id"])
        context = {
            "user_request": state["user_request"],
            "all_agent_results": state.get("agent_results") or {},
            "review_audit": state.get("review_result") or {},
        }
        updates = await self._execute_agent(
            agent_id="reporter",
            task_description=f"Synthesize comprehensive executive report for: {state['task_description']}",
            context=context,
            instructions="Create an organized executive report with structured sections and explicit missing evidence tracking.",
            state=state,
        )

        report_res = updates.get("agent_results", {}).get("reporter", {})
        updates["final_output"] = report_res
        updates["execution_status"] = "COMPLETED"
        return updates
