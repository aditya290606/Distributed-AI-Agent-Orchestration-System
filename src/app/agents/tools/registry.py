"""Controlled tool registration and permission validation foundation."""

import logging
from collections.abc import Callable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.core.exceptions import ToolRegistrationError

logger = logging.getLogger("app.agents.tools.registry")


class ToolPermission(StrEnum):
    """Permission boundaries governing tool invocation capabilities."""

    READ_ONLY = "READ_ONLY"
    COMPUTE = "COMPUTE"
    RESTRICTED = "RESTRICTED"


class ToolDefinition(BaseModel):
    """Metadata specification and schema defining a controlled agent tool."""

    name: str = Field(..., min_length=1, max_length=50, description="Unique tool identifier")
    description: str = Field(..., min_length=1, description="Functional summary of tool capability")
    input_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema specification for parameter validation",
    )
    permission: ToolPermission = Field(
        default=ToolPermission.READ_ONLY,
        description="Permission boundary level assigned to this tool",
    )
    is_enabled: bool = Field(
        default=True,
        description="Whether this tool is active and available for execution",
    )


class ControlledToolRegistry:
    """Safe, controlled registry for tool definitions and bounded handler execution."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, Callable[..., Any]] = {}

    def register_tool(
        self,
        tool: ToolDefinition,
        handler: Callable[..., Any] | None = None,
    ) -> None:
        """Register a validated tool definition and its execution handler.

        Args:
            tool: ToolDefinition specification.
            handler: Optional callable executing the tool under sandboxed boundaries.

        Raises:
            ToolRegistrationError: If tool name is already registered or schema is invalid.
        """
        if tool.name in self._tools:
            raise ToolRegistrationError(
                message=f"Tool '{tool.name}' is already registered.",
                tool_name=tool.name,
            )

        self._tools[tool.name] = tool
        if handler is not None:
            self._handlers[tool.name] = handler

        logger.info(
            "Registered tool '%s' [permission=%s, enabled=%s]",
            tool.name,
            tool.permission.value,
            tool.is_enabled,
        )

    def get_tool(self, name: str) -> ToolDefinition:
        """Retrieve a registered tool definition by name.

        Args:
            name: Tool identifier.

        Returns:
            ToolDefinition.

        Raises:
            ToolRegistrationError: If tool is not found.
        """
        if name not in self._tools:
            raise ToolRegistrationError(
                message=f"Tool '{name}' is not registered.",
                tool_name=name,
            )
        return self._tools[name]

    def has_tool(self, name: str) -> bool:
        """Check if a tool exists in the registry."""
        return name in self._tools

    def list_tools(self) -> list[ToolDefinition]:
        """List all registered tool definitions."""
        return list(self._tools.values())

    async def execute_tool(self, name: str, params: dict[str, Any]) -> Any:
        """Execute a registered tool handler under strictly validated parameters.

        Args:
            name: Tool name.
            params: Parameters matching tool's input_schema.

        Returns:
            Result from tool handler.

        Raises:
            ToolRegistrationError: If tool not registered, disabled, or handler missing.
        """
        tool = self.get_tool(name)
        if not tool.is_enabled:
            raise ToolRegistrationError(
                message=f"Tool '{name}' is currently disabled.",
                tool_name=name,
            )

        handler = self._handlers.get(name)
        if handler is None:
            raise ToolRegistrationError(
                message=f"Tool '{name}' does not have an executable handler attached.",
                tool_name=name,
            )

        logger.info("Executing tool '%s' with permission=%s", name, tool.permission.value)
        # Execute handler (supports both async and sync callables)
        import inspect

        if inspect.iscoroutinefunction(handler):
            return await handler(**params)
        return handler(**params)


_default_tool_registry: ControlledToolRegistry | None = None


def get_tool_registry() -> ControlledToolRegistry:
    """Return the singleton ControlledToolRegistry instance."""
    global _default_tool_registry
    if _default_tool_registry is None:
        _default_tool_registry = ControlledToolRegistry()
    return _default_tool_registry
