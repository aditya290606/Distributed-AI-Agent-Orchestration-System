"""Shared pytest fixtures for the test suite."""

from collections.abc import AsyncGenerator, Generator
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.dependencies import get_db
from app.main import create_application


@pytest.fixture
def test_settings() -> Settings:
    """Fixture providing isolated settings configured for automated testing."""
    return Settings(
        app_name="Distributed AI Agent Orchestration System",
        app_version="0.1.0",
        api_version="v1",
        environment="test",
        debug=True,
        log_level="DEBUG",
        gemini_api_key="test-gemini-key",
        celery_task_always_eager=True,
        postgres_db="test_agent_orchestrator",
    )


@pytest.fixture
def client(test_settings: Settings) -> Generator[TestClient, None, None]:
    """Fixture providing a FastAPI TestClient configured with test settings and mocked DB."""
    app = create_application()
    app.dependency_overrides[get_settings] = lambda: test_settings

    async def _mock_get_db() -> AsyncGenerator[AsyncSession, None]:
        mock_session = AsyncMock(spec=AsyncSession)
        yield mock_session

    app.dependency_overrides[get_db] = _mock_get_db

    yield TestClient(app)
