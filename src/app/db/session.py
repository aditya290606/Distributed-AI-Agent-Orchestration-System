"""SQLAlchemy 2.0 asynchronous engine and session factory management."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings

logger = logging.getLogger("app.db.session")

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    """Return the global AsyncEngine instance, initializing it if necessary."""
    global _engine
    if _engine is None:
        cfg = settings or get_settings()
        url = cfg.async_database_url

        # Extra connect args for MySQL asyncmy if applicable
        connect_args: dict[str, object] = {}
        if "asyncmy" in url:
            connect_args["charset"] = "utf8mb4"

        _engine = create_async_engine(
            url,
            pool_size=cfg.db_pool_size,
            max_overflow=cfg.db_max_overflow,
            pool_timeout=cfg.db_pool_timeout,
            pool_recycle=cfg.db_pool_recycle,
            pool_pre_ping=True,
            echo=cfg.debug,
            connect_args=connect_args,
        )
        logger.info(
            "AsyncEngine initialized for host=%s:%s db=%s (pool_size=%d, max_overflow=%d)",
            cfg.db_host,
            cfg.db_port,
            cfg.db_name,
            cfg.db_pool_size,
            cfg.db_max_overflow,
        )
    return _engine


def get_session_factory(
    settings: Settings | None = None,
) -> async_sessionmaker[AsyncSession]:
    """Return the global async_sessionmaker factory."""
    global _session_factory
    if _session_factory is None:
        engine = get_engine(settings)
        _session_factory = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            autoflush=False,
            expire_on_commit=False,
        )
    return _session_factory


@asynccontextmanager
async def get_session(
    settings: Settings | None = None,
) -> AsyncGenerator[AsyncSession, None]:
    """Context manager yielding an isolated AsyncSession with automatic transaction management."""
    factory = get_session_factory(settings)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def close_database_connections() -> None:
    """Gracefully dispose all connection pools during application shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        logger.info("Disposing database connection pool...")
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("Database connection pool disposed.")


async def check_database_health(timeout_seconds: float = 2.0) -> bool:
    """Execute a lightweight SELECT 1 query to verify active MySQL connectivity.

    Args:
        timeout_seconds: Maximum seconds to wait for a database response.

    Returns:
        bool: True if database responded successfully, False otherwise.
    """
    try:
        async with asyncio.timeout(timeout_seconds):
            async with get_session() as session:
                result = await session.execute(text("SELECT 1"))
                return result.scalar() == 1
    except Exception as exc:
        logger.warning("Database health check ping failed: %s", str(exc))
        return False
