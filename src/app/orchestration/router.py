"""Conditional routing functions for LangGraph workflow orchestration."""

import logging
from typing import Literal

from langgraph.graph import END

from app.orchestration.state import NexusState

logger = logging.getLogger("app.orchestration.router")


def route_after_supervisor(
    state: NexusState,
) -> Literal["planner", "researcher", "analyst", "coder", "reporter", "__end__"]:
    """Determine initial transition based on supervisor classification."""
    if state.get("execution_status") == "FAILED" or state.get("errors"):
        logger.warning(
            "[Workflow %s] Routing to END due to supervisor failure", state["workflow_id"]
        )
        return END

    selected = state.get("selected_agents", [])

    # If supervisor determined a simple task with a single specialist, bypass planner
    if len(selected) == 1:
        specialist = selected[0]
        if specialist in ("researcher", "analyst", "coder", "reporter"):
            logger.info(
                "[Workflow %s] Simple task detected. Bypassing planner -> routing to '%s'",
                state["workflow_id"],
                specialist,
            )
            return specialist  # type: ignore[return-value]

    # Default for complex tasks: proceed to task decomposition
    logger.info(
        "[Workflow %s] Routing to Planner Node for task decomposition", state["workflow_id"]
    )
    return "planner"


def route_after_plan_validator(
    state: NexusState,
) -> (
    list[str]
    | Literal["planner", "researcher", "analyst", "coder", "reviewer", "reporter", "__end__"]
):
    """Determine routing after plan validation: loops back to planner on repair, or dispatches specialists."""
    status = state.get("execution_status")

    if status == "PLAN_INVALID":
        logger.info(
            "[Workflow %s] Plan validation failed. Routing back to Planner Node for bounded repair.",
            state["workflow_id"],
        )
        return "planner"

    if status in ("PLAN_REPAIR_EXHAUSTED", "FAILED") or state.get("errors"):
        logger.warning(
            "[Workflow %s] Plan validation failed or repair exhausted. Routing to Reporter.",
            state["workflow_id"],
        )
        return "reporter"

    # Plan is valid: dispatch specialist nodes
    return route_after_planner(state)


def route_after_planner(
    state: NexusState,
) -> list[str] | Literal["researcher", "analyst", "coder", "reviewer", "__end__"]:
    """Determine first execution stage or parallel dispatch following planner decomposition."""
    if state.get("execution_status") == "FAILED" or state.get("errors"):
        logger.warning("[Workflow %s] Routing to END due to planner failure", state["workflow_id"])
        return END

    pending = list(state.get("pending_agents", []))

    # Parallel Execution: if both researcher and analyst are requested without dependencies, fan out
    if "researcher" in pending and "analyst" in pending and "coder" in pending:
        logger.info(
            "[Workflow %s] Parallel fan-out: dispatching ['researcher', 'analyst'] concurrently",
            state["workflow_id"],
        )
        return ["researcher", "analyst"]

    if "researcher" in pending:
        logger.info("[Workflow %s] Routing to Research Node", state["workflow_id"])
        return "researcher"

    if "analyst" in pending:
        logger.info("[Workflow %s] Routing to Analysis Node", state["workflow_id"])
        return "analyst"

    if "coder" in pending:
        logger.info("[Workflow %s] Routing to Coding Node", state["workflow_id"])
        return "coder"

    logger.info(
        "[Workflow %s] No specialists pending. Routing directly to Reviewer", state["workflow_id"]
    )
    return "reviewer"


def route_after_specialist(
    state: NexusState,
) -> Literal["analyst", "coder", "researcher", "reviewer", "__end__"]:
    """Route sequentially across pending specialist agents or transition to reviewer."""
    if state.get("execution_status") == "FAILED" and not state.get("agent_results"):
        return END

    pending = state.get("pending_agents", [])

    if "analyst" in pending:
        logger.info(
            "[Workflow %s] Specialist transition -> routing to Analysis Node", state["workflow_id"]
        )
        return "analyst"

    if "coder" in pending:
        logger.info(
            "[Workflow %s] Specialist transition -> routing to Coding Node", state["workflow_id"]
        )
        return "coder"

    if "researcher" in pending:
        logger.info(
            "[Workflow %s] Specialist transition -> routing to Research Node", state["workflow_id"]
        )
        return "researcher"

    # All specialists complete -> transition to quality review
    logger.info(
        "[Workflow %s] All specialist tasks complete -> routing to Reviewer Node",
        state["workflow_id"],
    )
    return "reviewer"


def route_after_reviewer(
    state: NexusState,
) -> Literal["coder", "planner", "reporter", "__end__"]:
    """Evaluate reviewer verdict and orchestrate review/revision loop."""
    review_res = state.get("review_result") or {}
    verdict = review_res.get("verdict", "ACCEPT")
    revision_count = state.get("revision_count", 0)
    max_revisions = state.get("max_revisions", 3)

    if verdict == "ACCEPT":
        logger.info(
            "[Workflow %s] Reviewer verdict: ACCEPT. Routing to Report Generation Node",
            state["workflow_id"],
        )
        return "reporter"

    if verdict == "REVISE":
        if revision_count < max_revisions:
            logger.info(
                "[Workflow %s] Reviewer verdict: REVISE (Iteration %d/%d). Routing back to Coder",
                state["workflow_id"],
                revision_count,
                max_revisions,
            )
            # Route back to coder if code was generated, otherwise planner
            if "coder" in state.get("completed_agents", []):
                return "coder"
            return "planner"

        logger.warning(
            "[Workflow %s] Maximum revisions reached (%d/%d). Terminating revision loop -> routing to Reporter",
            state["workflow_id"],
            revision_count,
            max_revisions,
        )
        return "reporter"

    # REJECT or unrecognized verdict
    logger.info(
        "[Workflow %s] Reviewer verdict: %s. Routing to Reporter for controlled finalization",
        state["workflow_id"],
        verdict,
    )
    return "reporter"
