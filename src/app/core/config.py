"""Centralized application configuration management powered by Pydantic v2 Settings."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env files."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application Core
    app_name: str = Field(
        default="Distributed AI Agent Orchestration System",
        description="User-facing application name",
    )
    app_version: str = Field(default="0.1.0", description="Semantic application version")
    environment: Literal["development", "staging", "production", "test"] = Field(
        default="development",
        description="Deployment runtime environment",
    )
    debug: bool = Field(default=False, description="Enable debug mode and verbose outputs")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Global logging level",
    )
    host: str = Field(default="0.0.0.0", description="API server bind host")
    port: int = Field(default=8000, description="API server bind port")
    secret_key: str = Field(
        default="development-insecure-secret-key-change-in-production-min32chars",
        description="Cryptographic secret key for session signing and tokens",
    )
    allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost:8000"],
        description="Allowed CORS origins",
    )

    # Google Gemini API Settings
    gemini_api_key: str = Field(
        default="",
        description="Google Gemini API Key for model invocations",
    )
    gemini_model: str = Field(
        default="gemini-2.5-flash",
        description="Default Gemini LLM model identifier",
    )
    gemini_embedding_model: str = Field(
        default="text-embedding-004",
        description="Gemini text embedding model identifier",
    )
    llm_temperature: float = Field(
        default=0.2,
        ge=0.0,
        le=2.0,
        description="Sampling temperature for LLM responses",
    )
    llm_max_output_tokens: int = Field(
        default=4096,
        gt=0,
        description="Maximum tokens generated per LLM response",
    )

    # Database Settings (PostgreSQL)
    postgres_user: str = Field(default="postgres", description="PostgreSQL database username")
    postgres_password: str = Field(default="postgres", description="PostgreSQL database password")
    postgres_server: str = Field(default="localhost", description="PostgreSQL server host")
    postgres_port: int = Field(default=5432, description="PostgreSQL server port")
    postgres_db: str = Field(default="agent_orchestrator", description="PostgreSQL database name")
    database_url: str | PostgresDsn | None = Field(
        default=None,
        description="Complete database connection URL (computed dynamically if not specified)",
    )

    # Redis Broker & Cache Settings
    redis_host: str = Field(default="localhost", description="Redis host")
    redis_port: int = Field(default=6379, description="Redis port")
    redis_db: int = Field(default=0, description="Redis database index")
    redis_password: str | None = Field(default=None, description="Optional Redis auth password")
    redis_url: str | RedisDsn | None = Field(
        default=None,
        description="Complete Redis connection URL",
    )

    # Celery Distributed Task Settings
    celery_broker_url: str = Field(
        default="redis://localhost:6379/1",
        description="Celery broker connection URL",
    )
    celery_result_backend: str = Field(
        default="redis://localhost:6379/2",
        description="Celery result backend URL",
    )
    celery_task_always_eager: bool = Field(
        default=False,
        description="Execute Celery tasks locally and synchronously (useful for test suites)",
    )

    # Observability & Tracing (OpenTelemetry)
    otel_service_name: str = Field(
        default="agent-orchestrator-backend",
        description="OpenTelemetry service name",
    )
    otel_exporter_otlp_endpoint: str = Field(
        default="http://localhost:4317",
        description="OTLP collector gRPC/HTTP endpoint",
    )
    otel_traces_enabled: bool = Field(
        default=False,
        description="Enable OpenTelemetry distributed tracing export",
    )

    @property
    def is_production(self) -> bool:
        """Check whether current environment is production."""
        return self.environment == "production"

    @property
    def resolved_database_url(self) -> str:
        """Return the configured database URL or construct one from components."""
        if self.database_url:
            return str(self.database_url)
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_server}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def resolved_redis_url(self) -> str:
        """Return the configured Redis URL or construct one from components."""
        if self.redis_url:
            return str(self.redis_url)
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Provide a cached singleton instance of application settings."""
    return Settings()
