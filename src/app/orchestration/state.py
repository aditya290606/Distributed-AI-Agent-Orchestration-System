"""Typed shared workflow state and reducers for LangGraph orchestration."""

import operator
from typing import Annotated, Any, TypedDict


def merge_dicts(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any]:
    """Reducer that safely merges dictionary keys without mutating operands."""
    res = dict(a or {})
    if b:
        res.update(b)
    return res


def append_unique(a: list[str] | None, b: list[str] | str | None) -> list[str]:
    """Reducer that appends items while maintaining chronological insertion and uniqueness."""
    res = list(a or [])
    if b:
        items = [b] if isinstance(b, str) else list(b)
        for item in items:
            if item not in res:
                res.append(item)
    return res


class NexusState(TypedDict):
    """Shared state container propagating execution context across LangGraph nodes."""

    # Workflow Identity & Input
    workflow_id: str
    task_id: str | None
    user_request: str
    task_description: str

    # Planning & Agent Selection
    execution_plan: dict[str, Any] | None
    selected_agents: list[str]
    completed_agents: Annotated[list[str], append_unique]
    pending_agents: list[str]

    # Execution Deliverables (Reducer prevents concurrent overwrites)
    agent_results: Annotated[dict[str, Any], merge_dicts]
    execution_status: str
    current_agent: str | None

    # Quality Assurance & Revision Loops
    review_result: dict[str, Any] | None
    revision_count: int
    max_revisions: int

    # Final Output Deliverables
    final_output: dict[str, Any] | str | None

    # Telemetry, Auditing & Error Handling
    errors: Annotated[list[str], operator.add]
    execution_metadata: Annotated[dict[str, Any], merge_dicts]


def create_initial_state(
    workflow_id: str,
    user_request: str,
    task_description: str | None = None,
    task_id: str | None = None,
    max_revisions: int = 3,
) -> NexusState:
    """Factory creating an initialized NexusState for a new workflow run."""
    desc = task_description or user_request
    return {
        "workflow_id": workflow_id,
        "task_id": task_id,
        "user_request": user_request,
        "task_description": desc,
        "execution_plan": None,
        "selected_agents": [],
        "completed_agents": [],
        "pending_agents": [],
        "agent_results": {},
        "execution_status": "INITIALIZED",
        "current_agent": None,
        "review_result": None,
        "revision_count": 0,
        "max_revisions": max_revisions,
        "final_output": None,
        "errors": [],
        "execution_metadata": {},
    }
