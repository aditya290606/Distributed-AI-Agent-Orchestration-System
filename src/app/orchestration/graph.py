"""StateGraph assembly and compilation for NEXUS AI multi-agent orchestration."""

import logging
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.orchestration.nodes import OrchestrationNodes
from app.orchestration.router import (
    route_after_plan_validator,
    route_after_reviewer,
    route_after_specialist,
    route_after_supervisor,
)
from app.orchestration.state import NexusState

logger = logging.getLogger("app.orchestration.graph")


def build_workflow_graph(
    nodes: OrchestrationNodes | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> Any:
    """Construct and compile the multi-agent orchestration StateGraph.

    Args:
        nodes: OrchestrationNodes container holding node handlers.
        checkpointer: Optional checkpointer for state retention and debugging.

    Returns:
        Compiled StateGraph instance ready for async invocation.
    """
    node_handlers = nodes or OrchestrationNodes()

    workflow = StateGraph(NexusState)

    # 1. Register Nodes
    workflow.add_node("supervisor", node_handlers.supervisor_node)
    workflow.add_node("planner", node_handlers.planner_node)
    workflow.add_node("plan_validator", node_handlers.plan_validation_node)
    workflow.add_node("researcher", node_handlers.research_node)
    workflow.add_node("analyst", node_handlers.analysis_node)
    workflow.add_node("coder", node_handlers.coding_node)
    workflow.add_node("reviewer", node_handlers.reviewer_node)
    workflow.add_node("reporter", node_handlers.report_node)

    # 2. Register Edges
    workflow.add_edge(START, "supervisor")

    # Supervisor conditional routing
    workflow.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        ["planner", "researcher", "analyst", "coder", "reporter", END],
    )

    # Planner transitions to Plan Validator
    workflow.add_edge("planner", "plan_validator")

    # Plan Validator conditional routing (supports bounded repair loop or specialist dispatch)
    workflow.add_conditional_edges(
        "plan_validator",
        route_after_plan_validator,
        ["planner", "researcher", "analyst", "coder", "reviewer", "reporter", END],
    )

    # Specialist conditional routing
    workflow.add_conditional_edges(
        "researcher",
        route_after_specialist,
        ["analyst", "coder", "researcher", "reviewer", END],
    )
    workflow.add_conditional_edges(
        "analyst",
        route_after_specialist,
        ["analyst", "coder", "researcher", "reviewer", END],
    )
    workflow.add_conditional_edges(
        "coder",
        route_after_specialist,
        ["analyst", "coder", "researcher", "reviewer", END],
    )

    # Reviewer conditional routing (evaluates ACCEPT / REVISE feedback loops)
    workflow.add_conditional_edges(
        "reviewer",
        route_after_reviewer,
        ["coder", "planner", "reporter", END],
    )

    # Report completion
    workflow.add_edge("reporter", END)

    compiled_graph = workflow.compile(checkpointer=checkpointer)
    logger.info("Compiled NEXUS AI LangGraph Multi-Agent StateGraph successfully")
    return compiled_graph
