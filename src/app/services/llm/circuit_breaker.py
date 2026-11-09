"""Lightweight circuit breaker guarding external LLM provider calls."""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.core.exceptions import LLMError

logger = logging.getLogger("app.services.llm.circuit_breaker")


class CircuitState(StrEnum):
    """Operational states of the circuit breaker."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpenError(LLMError):
    """Raised when an external call is rejected because the circuit breaker is OPEN."""

    def __init__(
        self,
        message: str = "LLM circuit breaker is OPEN. Upstream calls temporarily suspended to protect capacity.",
        model: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message=message, model=model)
        self.code = "CIRCUIT_BREAKER_OPEN"
        self.retry_after_seconds = retry_after_seconds


class CircuitBreaker:
    """Protects external Gemini LLM calls against cascade failures, quota exhaustions, and outages."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout_seconds: float = 30.0,
        half_open_success_threshold: int = 2,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout_seconds
        self.half_open_success_threshold = half_open_success_threshold

        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._half_open_successes = 0
        self._opened_at: datetime | None = None
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        """Return current circuit state, evaluating recovery timeout dynamically."""
        if self._state == CircuitState.OPEN and self._opened_at:
            elapsed = (datetime.now(UTC) - self._opened_at).total_seconds()
            if elapsed >= self.recovery_timeout:
                logger.info(
                    "Circuit breaker recovery timeout reached (%.1fs). Transitioning OPEN -> HALF_OPEN",
                    elapsed,
                )
                self._state = CircuitState.HALF_OPEN
                self._half_open_successes = 0
        return self._state

    async def can_execute(self) -> bool:
        """Check whether the circuit allows a request to proceed."""
        async with self._lock:
            return self.state != CircuitState.OPEN

    async def record_success(self) -> None:
        """Register a successful external LLM call."""
        async with self._lock:
            current_state = self.state
            if current_state == CircuitState.HALF_OPEN:
                self._half_open_successes += 1
                logger.info(
                    "Circuit probe trial succeeded (%d/%d)",
                    self._half_open_successes,
                    self.half_open_success_threshold,
                )
                if self._half_open_successes >= self.half_open_success_threshold:
                    logger.info(
                        "Circuit breaker probe threshold satisfied. Transitioning HALF_OPEN -> CLOSED"
                    )
                    self._state = CircuitState.CLOSED
                    self._consecutive_failures = 0
                    self._opened_at = None
            elif current_state == CircuitState.CLOSED:
                self._consecutive_failures = 0

    async def record_failure(self, exc: Exception) -> None:
        """Register a failed external call and trigger state transition if threshold exceeded."""
        async with self._lock:
            self._consecutive_failures += 1
            now = datetime.now(UTC)
            logger.warning(
                "Circuit breaker recorded failure #%d: %s",
                self._consecutive_failures,
                exc,
            )

            if self._state in (CircuitState.CLOSED, CircuitState.HALF_OPEN) and (
                self._consecutive_failures >= self.failure_threshold
                or self._state == CircuitState.HALF_OPEN
            ):
                logger.error(
                    "Circuit breaker threshold reached (%d failures). Transitioning to OPEN. Calls blocked for %.1fs",
                    self._consecutive_failures,
                    self.recovery_timeout,
                )
                self._state = CircuitState.OPEN
                self._opened_at = now

    async def execute(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute callable protected by the circuit breaker."""
        if not await self.can_execute():
            remaining = 0.0
            if self._opened_at:
                elapsed = (datetime.now(UTC) - self._opened_at).total_seconds()
                remaining = max(0.0, round(self.recovery_timeout - elapsed, 1))
            raise CircuitBreakerOpenError(
                message=f"LLM circuit breaker is OPEN. Upstream calls suspended (retry in {remaining}s).",
                retry_after_seconds=remaining,
            )

        try:
            result = await func(*args, **kwargs)
            await self.record_success()
            return result
        except Exception as exc:
            # Only count transient or operational failures toward tripping the breaker
            # (Permanent user input errors like 400 or bad prompts shouldn't trip the infrastructure breaker)
            from app.core.exceptions import LLMAuthenticationError, LLMInvalidRequestError

            if not isinstance(exc, (LLMAuthenticationError, LLMInvalidRequestError)):
                await self.record_failure(exc)
            raise

    def reset(self) -> None:
        """Reset circuit breaker to default CLOSED state."""
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._half_open_successes = 0
        self._opened_at = None

    def force_open(self) -> None:
        """Force circuit breaker into OPEN state (for testing / fault-injection)."""
        self._state = CircuitState.OPEN
        self._opened_at = datetime.now(UTC)
        self._consecutive_failures = self.failure_threshold
