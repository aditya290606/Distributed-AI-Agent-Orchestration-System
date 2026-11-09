"""Unit tests for core configuration, logging, exceptions, and foundational schemas."""

from app.core.config import Settings
from app.core.exceptions import (
    AgentExecutionError,
    AppException,
    ConfigurationError,
    DistributedTaskError,
    OrchestrationError,
    ResourceNotFoundError,
)
from app.core.logging import setup_logging
from app.schemas.health import HealthCheckResponse


def test_default_settings():
    """Verify that default settings instantiate with expected defaults."""
    settings = Settings()
    assert settings.app_name == "Distributed AI Agent Orchestration System"
    assert settings.app_version == "0.1.0"
    assert settings.environment == "development"
    assert not settings.is_production
    assert "mysql+asyncmy://" in settings.async_database_url
    assert "nexus_ai" in settings.async_database_url
    assert "postgresql+asyncpg://" in settings.resolved_database_url
    assert "redis://" in settings.resolved_redis_url


def test_mysql_database_url_and_special_character_escaping():
    """Verify safe MySQL URL generation and URL encoding of special password characters."""
    settings = Settings(
        DB_HOST="db.internal",
        DB_PORT=3307,
        DB_USER="admin_user",
        DB_PASSWORD="P@ssw#rd/123!&*",
        DB_NAME="production_nexus",
        DB_POOL_SIZE=15,
        DB_MAX_OVERFLOW=25,
        DB_POOL_TIMEOUT=45,
        DB_POOL_RECYCLE=3600,
    )
    url = settings.async_database_url
    assert "mysql+asyncmy://" in url
    assert "admin_user:" in url
    # Ensure special characters are percent-encoded
    assert "P%40ssw%23rd%2F123%21%26%2A" in url
    assert "@db.internal:3307/production_nexus" in url
    assert settings.db_pool_size == 15
    assert settings.db_max_overflow == 25
    assert settings.db_pool_timeout == 45
    assert settings.db_pool_recycle == 3600


def test_custom_test_settings(test_settings: Settings):
    """Verify that test fixture settings override correctly."""
    assert test_settings.environment == "test"
    assert test_settings.debug is True
    assert test_settings.log_level == "DEBUG"
    assert test_settings.gemini_api_key == "test-gemini-key"
    assert test_settings.celery_task_always_eager is True
    assert "test_agent_orchestrator" in test_settings.resolved_database_url


def test_logging_setup(test_settings: Settings):
    """Verify that logging setup executes cleanly without exceptions."""
    logger = setup_logging(test_settings)
    assert logger is not None
    assert logger.name == "app"


def test_app_exceptions():
    """Verify exception hierarchy and JSON dictionary serialization."""
    base_err = AppException("Something failed", code="GENERIC_ERROR", details={"key": "val"})
    assert base_err.to_dict() == {
        "error": {
            "code": "GENERIC_ERROR",
            "message": "Something failed",
            "details": {"key": "val"},
        }
    }

    config_err = ConfigurationError("Missing key")
    assert config_err.code == "CONFIGURATION_ERROR"

    agent_err = AgentExecutionError("Tool failed", agent_name="researcher")
    assert agent_err.code == "AGENT_EXECUTION_ERROR"
    assert agent_err.details["agent_name"] == "researcher"

    orch_err = OrchestrationError("Recursion exceeded")
    assert orch_err.code == "ORCHESTRATION_ERROR"

    res_err = ResourceNotFoundError("AgentTask", "task-123")
    assert res_err.code == "RESOURCE_NOT_FOUND"
    assert res_err.details["resource"] == "AgentTask"
    assert res_err.details["resource_id"] == "task-123"

    dist_err = DistributedTaskError("celery-456", "Worker crashed")
    assert dist_err.code == "DISTRIBUTED_TASK_ERROR"
    assert dist_err.details["task_id"] == "celery-456"


def test_health_check_schema():
    """Verify HealthCheckResponse schema serialization and default timestamp generation."""
    response = HealthCheckResponse(
        status="healthy",
        app_name="Test Orchestrator",
        version="0.1.0",
        environment="test",
    )
    assert response.status == "healthy"
    assert response.app_name == "Test Orchestrator"
    assert response.version == "0.1.0"
    assert response.timestamp is not None
    data = response.model_dump()
    assert "timestamp" in data
