"""Centralized structured error classification, retry policy, and backoff engine."""

import logging
import random
from dataclasses import dataclass
from enum import StrEnum

from app.core.exceptions import (
    AgentNotFoundError,
    AgentValidationError,
    LLMAuthenticationError,
    LLMInvalidRequestError,
    LLMRateLimitError,
    LLMStructuredOutputError,
    LLMTimeoutError,
)

logger = logging.getLogger("app.services.recovery.retry_policy")


class ErrorCategory(StrEnum):
    """Categorization of task execution failures."""

    TRANSIENT = "TRANSIENT"
    PERMANENT = "PERMANENT"
    TIMEOUT = "TIMEOUT"
    WORKER_FAILURE = "WORKER_FAILURE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class RetryDecision:
    """Outcome of evaluating whether an exception qualifies for automatic retry."""

    should_retry: bool
    delay_seconds: float
    error_category: ErrorCategory
    failure_reason: str
    retry_count: int
    max_retries: int


class RetryPolicy:
    """Evaluates exceptions, classifies failure modes, and computes bounded exponential backoff."""

    def __init__(
        self,
        default_max_retries: int = 3,
        initial_delay_seconds: float = 4.0,
        max_delay_seconds: float = 120.0,
        enable_jitter: bool = True,
    ) -> None:
        self.default_max_retries = default_max_retries
        self.initial_delay = initial_delay_seconds
        self.max_delay = max_delay_seconds
        self.enable_jitter = enable_jitter

    def classify_error(self, exc: Exception) -> ErrorCategory:
        """Categorize an exception into TRANSIENT, PERMANENT, or TIMEOUT failure modes."""
        exc_str = str(exc).lower()

        # 1. Permanent failures: DO NOT RETRY
        if isinstance(
            exc,
            (
                AgentNotFoundError,
                AgentValidationError,
                LLMAuthenticationError,
                LLMInvalidRequestError,
            ),
        ):
            return ErrorCategory.PERMANENT

        if "not found" in exc_str and "agent" in exc_str:
            return ErrorCategory.PERMANENT
        if "invalid" in exc_str and ("schema" in exc_str or "request" in exc_str):
            return ErrorCategory.PERMANENT

        # 2. Timeouts
        if isinstance(exc, (TimeoutError, LLMTimeoutError)) or "timeout" in exc_str:
            return ErrorCategory.TIMEOUT

        # 3. Transient failures: safe to retry
        if isinstance(exc, (LLMRateLimitError, LLMStructuredOutputError, ConnectionError, OSError)):
            return ErrorCategory.TRANSIENT

        if any(
            keyword in exc_str
            for keyword in [
                "rate limit",
                "429",
                "503",
                "connection",
                "refused",
                "reset",
                "broker",
                "deadlock",
                "restart",
            ]
        ):
            return ErrorCategory.TRANSIENT

        # Default fallback
        return ErrorCategory.UNKNOWN

    def compute_backoff(self, retry_count: int) -> float:
        """Calculate exponential backoff delay with optional random jitter.

        Formula: min(max_delay, initial_delay * (2 ** retry_count)) + uniform(0, 1)
        """
        raw_delay = self.initial_delay * (2**retry_count)
        bounded = min(raw_delay, self.max_delay)
        if self.enable_jitter:
            jitter = random.uniform(0.1, 1.0)
            return round(bounded + jitter, 2)
        return round(bounded, 2)

    def evaluate(
        self,
        exc: Exception,
        current_retry_count: int,
        max_retries: int | None = None,
    ) -> RetryDecision:
        """Determine whether to retry a failed task based on exception category and attempt budget.

        Args:
            exc: Exception encountered during task execution.
            current_retry_count: Number of retries already performed.
            max_retries: Optional maximum allowed retries (defaults to self.default_max_retries).

        Returns:
            Structured RetryDecision.
        """
        limit = max_retries if max_retries is not None else self.default_max_retries
        category = self.classify_error(exc)
        reason = f"{exc.__class__.__name__}: {exc!s}"

        if category == ErrorCategory.PERMANENT:
            logger.info("Permanent error encountered: %s. Rejecting retry.", reason)
            return RetryDecision(
                should_retry=False,
                delay_seconds=0.0,
                error_category=category,
                failure_reason=reason,
                retry_count=current_retry_count,
                max_retries=limit,
            )

        if current_retry_count >= limit:
            logger.warning(
                "Exhausted max retries (%d/%d) for error [%s]: %s",
                current_retry_count,
                limit,
                category.value,
                reason,
            )
            return RetryDecision(
                should_retry=False,
                delay_seconds=0.0,
                error_category=category,
                failure_reason=reason,
                retry_count=current_retry_count,
                max_retries=limit,
            )

        delay = self.compute_backoff(current_retry_count)
        logger.info(
            "Scheduling retry %d/%d in %.2fs for [%s] error: %s",
            current_retry_count + 1,
            limit,
            delay,
            category.value,
            reason,
        )
        return RetryDecision(
            should_retry=True,
            delay_seconds=delay,
            error_category=category,
            failure_reason=reason,
            retry_count=current_retry_count,
            max_retries=limit,
        )
