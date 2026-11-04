"""Shared pytest fixtures for the test suite."""

import pytest

from app.core.config import Settings


@pytest.fixture
def test_settings() -> Settings:
    """Fixture providing isolated settings configured for automated testing."""
    return Settings(
        environment="test",
        debug=True,
        log_level="DEBUG",
        gemini_api_key="test-gemini-key",
        postgres_db="test_agent_orchestrator",
        celery_task_always_eager=True,
        otel_traces_enabled=False,
    )
