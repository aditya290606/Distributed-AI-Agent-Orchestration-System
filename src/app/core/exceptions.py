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


class ResourceNotFoundError(AppException):
    """Raised when a requested resource (task, workflow, run) is not found."""

    def __init__(self, resource: str, resource_id: str) -> None:
        details = {"resource": resource, "resource_id": resource_id}
        super().__init__(
            f"{resource} with ID '{resource_id}' was not found.",
            code="RESOURCE_NOT_FOUND",
            details=details,
        )


class AgentExecutionError(AppException):
    """Raised when an AI agent encounters an unrecoverable execution failure."""

    def __init__(
        self,
        message: str,
        agent_name: str | None = None,
        agent_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        ctx: dict[str, Any] = {}
        if agent_name:
            ctx["agent_name"] = agent_name
        if agent_id:
            ctx["agent_id"] = agent_id
        if details:
            ctx.update(details)
        super().__init__(message, code="AGENT_EXECUTION_ERROR", details=ctx)


class OrchestrationError(AppException):
    """Raised when the LangGraph multi-agent orchestration state machine fails."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="ORCHESTRATION_ERROR", details=details)


class DistributedTaskError(AppException):
    """Raised when background distributed worker execution encounters a failure."""

    def __init__(self, task_id: str, message: str, details: dict[str, Any] | None = None) -> None:
        ctx = {"task_id": task_id, **(details or {})}
        super().__init__(message, code="DISTRIBUTED_TASK_ERROR", details=ctx)


# ------------------------------------------------------------------------------
# LLM Service & Provider Exceptions (Milestone 2)
# ------------------------------------------------------------------------------


class LLMError(AppException):
    """Base exception for all LLM service and provider errors."""

    def __init__(
        self,
        message: str,
        code: str = "LLM_ERROR",
        model: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        ctx = {"model": model, **(details or {})} if model else (details or {})
        super().__init__(message, code=code, details=ctx)


class LLMAuthenticationError(LLMError):
    """Raised when LLM API credentials are missing, revoked, or unauthorized (HTTP 401/403)."""

    def __init__(
        self,
        message: str = "LLM authentication failed: Invalid or missing API key.",
        model: str | None = None,
    ) -> None:
        super().__init__(message, code="LLM_AUTHENTICATION_ERROR", model=model)


class LLMRateLimitError(LLMError):
    """Raised when LLM quota or rate limits are exceeded (HTTP 429)."""

    def __init__(
        self,
        message: str = "LLM rate limit or quota exceeded.",
        model: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        details = {"retry_after": retry_after} if retry_after is not None else None
        super().__init__(message, code="LLM_RATE_LIMIT_ERROR", model=model, details=details)


class LLMTimeoutError(LLMError):
    """Raised when LLM generation exceeds the configured deadline/timeout."""

    def __init__(
        self,
        message: str = "LLM generation request timed out.",
        model: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        details = {"timeout_seconds": timeout_seconds} if timeout_seconds is not None else None
        super().__init__(message, code="LLM_TIMEOUT_ERROR", model=model, details=details)


class LLMInvalidRequestError(LLMError):
    """Raised when prompt or generation parameters are invalid (HTTP 400)."""

    def __init__(
        self, message: str, model: str | None = None, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, code="LLM_INVALID_REQUEST_ERROR", model=model, details=details)


class LLMStructuredOutputError(LLMError):
    """Raised when model output fails to parse into the requested Pydantic schema."""

    def __init__(
        self,
        message: str,
        raw_response: str | None = None,
        validation_error: str | None = None,
        model: str | None = None,
    ) -> None:
        details = {
            "raw_response_snippet": raw_response[:500] if raw_response else None,
            "validation_error": validation_error,
        }
        super().__init__(message, code="LLM_STRUCTURED_OUTPUT_ERROR", model=model, details=details)


# ------------------------------------------------------------------------------
# Specialized Agent Exceptions (Milestone 3)
# ------------------------------------------------------------------------------


class AgentException(AppException):
    """Base exception for all agent framework errors."""

    def __init__(
        self,
        message: str,
        code: str = "AGENT_ERROR",
        agent_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        ctx = {"agent_id": agent_id, **(details or {})} if agent_id else (details or {})
        super().__init__(message, code=code, details=ctx)


class AgentNotFoundError(AgentException):
    """Raised when an requested agent identifier is not registered."""

    def __init__(self, agent_id: str) -> None:
        super().__init__(
            message=f"Agent '{agent_id}' is not registered in the agent registry.",
            code="AGENT_NOT_FOUND",
            agent_id=agent_id,
        )


class AgentRegistrationError(AgentException):
    """Raised when attempting to register an invalid or duplicate agent."""

    def __init__(self, message: str, agent_id: str | None = None) -> None:
        super().__init__(message, code="AGENT_REGISTRATION_ERROR", agent_id=agent_id)


class AgentValidationError(AgentException):
    """Raised when agent inputs or configuration fail validation."""

    def __init__(
        self,
        message: str,
        agent_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code="AGENT_VALIDATION_ERROR", agent_id=agent_id, details=details)


class ToolRegistrationError(AgentException):
    """Raised when a tool registration fails validation or conflicts."""

    def __init__(self, message: str, tool_name: str | None = None) -> None:
        ctx = {"tool_name": tool_name} if tool_name else None
        super().__init__(message, code="TOOL_REGISTRATION_ERROR", details=ctx)
