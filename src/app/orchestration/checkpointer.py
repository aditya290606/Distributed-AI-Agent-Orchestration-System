"""Checkpointing adapter and state persistence interfaces for LangGraph workflows."""

import logging
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

logger = logging.getLogger("app.orchestration.checkpointer")


class WorkflowCheckpointAdapter:
    """Adapter boundary managing workflow graph checkpointing and persistence.

    ARCHITECTURAL NOTICE:
    NEXUS AI utilizes MySQL as its persistent store. LangGraph does not feature an official
    production MySQL checkpointer out-of-the-box.

    - Development Checkpointing: Utilizes LangGraph's `MemorySaver` for in-process state step
      tracking, debugging, and review loop state retention. This checkpointer is strictly
      ephemeral and session-scoped.
    - True Durability: Persistent workflow status, task records, and deliverables are durably
      stored in MySQL tables (`workflows`, `tasks`, `agent_executions`) via SQLAlchemy 2.0.
    """

    def __init__(self, checkpointer: BaseCheckpointSaver | None = None) -> None:
        self._checkpointer = checkpointer or MemorySaver()
        self._is_durable = not isinstance(self._checkpointer, MemorySaver)
        if not self._is_durable:
            logger.debug(
                "Initialized WorkflowCheckpointAdapter with ephemeral MemorySaver. "
                "Graph step recovery is in-process only; durable state resides in MySQL."
            )

    @property
    def checkpointer(self) -> BaseCheckpointSaver:
        """Return the underlying LangGraph checkpointer instance."""
        return self._checkpointer

    @property
    def is_durable(self) -> bool:
        """Whether the checkpointer provides cross-process durable storage."""
        return self._is_durable

    @staticmethod
    def build_config(workflow_id: str) -> dict[str, Any]:
        """Construct standard LangGraph execution configuration with consistent thread_id."""
        return {
            "configurable": {
                "thread_id": str(workflow_id),
                "checkpoint_ns": "nexus_workflow",
            }
        }

    async def get_latest_checkpoint(self, workflow_id: str) -> dict[str, Any] | None:
        """Retrieve the latest checkpoint state dictionary for a given workflow_id."""
        config = self.build_config(workflow_id)
        tuple_data = await self._checkpointer.aget_tuple(config)
        if tuple_data and tuple_data.checkpoint:
            return tuple_data.checkpoint.get("channel_values")  # type: ignore[no-any-return]
        return None


_default_checkpointer_adapter: WorkflowCheckpointAdapter | None = None


def get_checkpoint_adapter() -> WorkflowCheckpointAdapter:
    """Return the shared singleton WorkflowCheckpointAdapter."""
    global _default_checkpointer_adapter
    if _default_checkpointer_adapter is None:
        _default_checkpointer_adapter = WorkflowCheckpointAdapter()
    return _default_checkpointer_adapter
