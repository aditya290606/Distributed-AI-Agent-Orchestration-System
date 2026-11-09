"""Redis client integration, connection validation, and health probe utilities."""

import contextlib
import logging
from typing import Any

import redis

from app.core.config import get_settings

logger = logging.getLogger("app.workers.redis")


def get_redis_connection(timeout: float | None = None) -> redis.Redis:
    """Create a Redis client instance from application settings.

    Args:
        timeout: Socket timeout in seconds. Defaults to settings.redis_timeout.

    Returns:
        Configured redis.Redis client.
    """
    settings = get_settings()
    t = timeout if timeout is not None else settings.redis_timeout
    return redis.Redis.from_url(
        settings.resolved_redis_url,
        socket_timeout=t,
        socket_connect_timeout=t,
        decode_responses=True,
    )


def check_redis_health(timeout: float = 1.5) -> dict[str, Any]:
    """Probe Redis broker connection and return diagnostic status.

    Args:
        timeout: Maximum seconds to wait before declaring Redis unreachable.

    Returns:
        Dictionary detailing connection status, latency, or error information.
    """
    settings = get_settings()
    client = get_redis_connection(timeout=timeout)
    try:
        ping_ok = client.ping()
        info = client.info(section="server")
        return {
            "status": "healthy" if ping_ok else "unhealthy",
            "redis_version": info.get("redis_version", "unknown"),
            "connected_clients": client.info(section="clients").get("connected_clients", 0),
            "broker_url": f"redis://{settings.redis_host}:{settings.redis_port}/{settings.redis_db}",
        }
    except Exception as e:
        logger.warning("Redis health probe failed: %s", e)
        return {
            "status": "unreachable",
            "error": str(e),
            "broker_url": f"redis://{settings.redis_host}:{settings.redis_port}/{settings.redis_db}",
        }
    finally:
        with contextlib.suppress(Exception):
            client.close()
