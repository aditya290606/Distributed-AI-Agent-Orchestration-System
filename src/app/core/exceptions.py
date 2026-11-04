"""Application domain exception hierarchy for robust error handling across layers."""

from typing import Any


class AppException(Exception):
    """Base exception for all application-specific exceptions."""

    def __init__(
        self,
        message: str,
        code: str = "INTERNAL_SERVER_ERROR",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Convert exception to dictionary representation for API responses."""
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            }
        }


class ConfigurationError(AppException):
    """Raised when environment or runtime configuration is invalid or missing."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="CONFIGURATION_ERROR", details=details)


class AgentExecutionError(AppException):
    """Raised when an AI agent encounters an unrecoverable execution failure."""

    def __init__(
        self, message: str, agent_name: str, details: dict[str, Any] | None = None
    ) -> None:
        ctx = {"agent_name": agent_name, **(details or {})}
        super().__init__(message, code="AGENT_EXECUTION_ERROR", details=ctx)


class OrchestrationError(AppException):
    """Raised when the LangGraph multi-agent orchestration state machine fails."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="ORCHESTRATION_ERROR", details=details)


class ResourceNotFoundError(AppException):
    """Raised when a requested resource (task, run, checkpoint) is not found."""

    def __init__(self, resource: str, resource_id: str) -> None:
        details = {"resource": resource, "resource_id": resource_id}
        super().__init__(
            f"{resource} with ID '{resource_id}' was not found.",
            code="RESOURCE_NOT_FOUND",
            details=details,
        )


class DistributedTaskError(AppException):
    """Raised when background distributed worker execution encounters a failure."""

    def __init__(self, task_id: str, message: str, details: dict[str, Any] | None = None) -> None:
        ctx = {"task_id": task_id, **(details or {})}
        super().__init__(message, code="DISTRIBUTED_TASK_ERROR", details=ctx)
