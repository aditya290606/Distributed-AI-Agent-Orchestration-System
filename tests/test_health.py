"""Automated integration tests for FastAPI root, health, and readiness endpoints."""

from unittest.mock import AsyncMock, patch

from fastapi import status
from fastapi.testclient import TestClient

from app.core.config import Settings


def test_root_endpoint(client: TestClient, test_settings: Settings):
    """Verify GET / returns 200 OK and expected welcome payload."""
    response = client.get("/")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["message"] == f"Welcome to {test_settings.app_name} API"
    assert data["app_name"] == test_settings.app_name
    assert data["version"] == test_settings.app_version
    assert data["docs_url"] == "/docs"


def test_health_endpoint(client: TestClient, test_settings: Settings):
    """Verify GET /api/v1/health returns 200 OK as a pure liveness probe."""
    response = client.get("/api/v1/health")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "healthy"
    assert data["app_name"] == test_settings.app_name
    assert data["api_version"] == test_settings.api_version
    assert data["environment"] == test_settings.environment
    assert "timestamp" in data


def test_readiness_endpoint_when_database_connected(client: TestClient):
    """Verify GET /api/v1/health/ready returns 200 OK when database health check succeeds."""
    with patch(
        "app.api.routes.health.check_database_health",
        new_callable=AsyncMock,
        return_value=True,
    ):
        response = client.get("/api/v1/health/ready")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "ready"
        assert "dependencies" in data
        assert data["dependencies"]["database"] == "connected"
        assert "redis" in data["dependencies"]
        assert "timestamp" in data


def test_readiness_endpoint_when_database_disconnected(client: TestClient):
    """Verify GET /api/v1/health/ready returns 503 Service Unavailable when database is unreachable."""
    with patch(
        "app.api.routes.health.check_database_health",
        new_callable=AsyncMock,
        return_value=False,
    ):
        response = client.get("/api/v1/health/ready")

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        data = response.json()
        assert data["status"] == "not_ready"
        assert "dependencies" in data
        assert data["dependencies"]["database"] == "disconnected"
        assert "Database is unreachable" in data["message"]


def test_not_found_endpoint(client: TestClient):
    """Verify that requests to unregistered routes return standard 404 Not Found."""
    response = client.get("/api/v1/nonexistent-route")
    assert response.status_code == status.HTTP_404_NOT_FOUND
