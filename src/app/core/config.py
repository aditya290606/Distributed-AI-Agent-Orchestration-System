"""Centralized application configuration management powered by Pydantic v2 Settings."""

import urllib.parse
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env files."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Core Application Settings
    app_name: str = Field(
        default="Distributed AI Agent Orchestration System",
        description="User-facing application name",
    )
    app_version: str = Field(
        default="0.1.0",
        description="Semantic application release version",
    )
    api_version: str = Field(
        default="v1",
        description="API route version prefix identifier",
    )
    environment: Literal["development", "staging", "production", "test"] = Field(
        default="development",
        description="Active runtime environment",
    )
    debug: bool = Field(
        default=False,
        description="Enable debug mode and detailed diagnostics",
    )
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

    # MySQL Database Configuration (Milestone 1)
    db_host: str = Field(
        default="localhost",
        alias="DB_HOST",
        description="MySQL database server host",
    )
    db_port: int = Field(
        default=3306,
        alias="DB_PORT",
        description="MySQL database server port",
    )
    db_user: str = Field(
        default="root",
        alias="DB_USER",
        description="MySQL database username",
    )
    db_password: str = Field(
        default="",
        alias="DB_PASSWORD",
        description="MySQL database password",
    )
    db_name: str = Field(
        default="nexus_ai",
        alias="DB_NAME",
        description="MySQL database name",
    )
    db_pool_size: int = Field(
        default=10,
        alias="DB_POOL_SIZE",
        description="SQLAlchemy async connection pool size",
    )
    db_max_overflow: int = Field(
        default=20,
        alias="DB_MAX_OVERFLOW",
        description="Maximum overflow connections beyond pool_size",
    )
    db_pool_timeout: int = Field(
        default=30,
        alias="DB_POOL_TIMEOUT",
        description="Seconds to wait before timing out on getting a connection from pool",
    )
    db_pool_recycle: int = Field(
        default=1800,
        alias="DB_POOL_RECYCLE",
        description="Seconds after which idle connections are refreshed (prevents MySQL timeouts)",
    )

    # Generic & Legacy Database Settings (Preserved for compatibility)
    database_url: str | None = Field(
        default=None,
        description="Complete database connection URL override",
    )
    postgres_user: str = Field(default="postgres", description="PostgreSQL database username")
    postgres_password: str = Field(default="postgres", description="PostgreSQL database password")
    postgres_server: str = Field(default="localhost", description="PostgreSQL server host")
    postgres_port: int = Field(default=5432, description="PostgreSQL server port")
    postgres_db: str = Field(default="agent_orchestrator", description="PostgreSQL database name")

    # Google Gemini API Settings (Milestone 2)
    gemini_api_key: str = Field(
        default="",
        alias="GEMINI_API_KEY",
        description="Google Gemini API Key for model invocations",
    )
    gemini_model: str = Field(
        default="gemini-2.5-flash",
        alias="GEMINI_MODEL",
        description="Default Gemini LLM model identifier",
    )
    gemini_timeout: float = Field(
        default=60.0,
        alias="GEMINI_TIMEOUT",
        ge=1.0,
        le=300.0,
        description="Per-request timeout for LLM calls in seconds",
    )
    gemini_max_retries: int = Field(
        default=3,
        alias="GEMINI_MAX_RETRIES",
        ge=0,
        le=10,
        description="Maximum retry attempts for transient Gemini API failures",
    )
    gemini_max_output_tokens: int = Field(
        default=4096,
        alias="GEMINI_MAX_OUTPUT_TOKENS",
        gt=0,
        le=32768,
        description="Maximum tokens generated per LLM response",
    )
    gemini_temperature: float = Field(
        default=0.2,
        alias="GEMINI_TEMPERATURE",
        ge=0.0,
        le=2.0,
        description="Sampling temperature for LLM responses (0.0 to 2.0)",
    )

    # Redis & Celery Distributed Task Settings (Milestone 6)
    redis_host: str = Field(default="localhost", alias="REDIS_HOST", description="Redis host")
    redis_port: int = Field(default=6379, alias="REDIS_PORT", description="Redis port")
    redis_db: int = Field(default=0, alias="REDIS_DB", description="Redis database index")
    redis_password: str | None = Field(
        default=None, alias="REDIS_PASSWORD", description="Optional Redis password"
    )
    redis_url: str | None = Field(
        default=None, alias="REDIS_URL", description="Complete Redis connection URL"
    )
    redis_timeout: float = Field(default=2.0, description="Redis connection timeout in seconds")
    celery_broker_url: str | None = Field(
        default=None,
        alias="CELERY_BROKER_URL",
        description="Celery message broker URL override",
    )
    celery_result_backend: str | None = Field(
        default=None,
        alias="CELERY_RESULT_BACKEND",
        description="Celery result backend URL override",
    )
    celery_task_always_eager: bool = Field(
        default=False,
        alias="CELERY_TASK_ALWAYS_EAGER",
        description="Execute Celery tasks synchronously (for test suites)",
    )

    @property
    def is_production(self) -> bool:
        """Check whether current environment is production."""
        return self.environment == "production"

    @property
    def has_gemini_key(self) -> bool:
        """Check whether a non-empty Gemini API key has been configured."""
        return bool(self.gemini_api_key and self.gemini_api_key.strip())

    @property
    def async_database_url(self) -> str:
        """Construct a safe asynchronous MySQL database URL using asyncmy driver.

        Properly escapes passwords with special characters via URL encoding.
        """
        if self.database_url:
            return self.database_url
        encoded_password = urllib.parse.quote_plus(self.db_password)
        auth = f"{self.db_user}:{encoded_password}@" if self.db_password else f"{self.db_user}@"
        return f"mysql+asyncmy://{auth}{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"

    @property
    def sync_database_url(self) -> str:
        """Construct a synchronous MySQL database URL for Alembic migrations using pymysql/sync driver."""
        encoded_password = urllib.parse.quote_plus(self.db_password)
        auth = f"{self.db_user}:{encoded_password}@" if self.db_password else f"{self.db_user}@"
        return f"mysql+pymysql://{auth}{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"

    @property
    def resolved_database_url(self) -> str:
        """Preserved for compatibility with existing tests and postgres placeholders."""
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_server}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def resolved_redis_url(self) -> str:
        """Return the configured Redis URL or construct one from components."""
        if self.redis_url:
            return self.redis_url
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def resolved_celery_broker_url(self) -> str:
        """Return the configured Celery broker URL or default to Redis DB 1."""
        if self.celery_broker_url:
            return self.celery_broker_url
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/1"

    @property
    def resolved_celery_result_backend(self) -> str:
        """Return the configured Celery result backend URL or default to Redis DB 2."""
        if self.celery_result_backend:
            return self.celery_result_backend
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/2"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Provide a cached singleton instance of application settings."""
    return Settings()
