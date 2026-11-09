"""Worker and distributed task infrastructure health probing services."""

import logging
from datetime import UTC, datetime
from typing import Any

from app.workers.redis_client import check_redis_health
from app.workers.worker_registry import list_active_workers

logger = logging.getLogger("app.workers.health")


def get_worker_health_summary(timeout: float = 1.0) -> dict[str, Any]:
    """Compile comprehensive health diagnostics for Redis broker and Celery worker fleet.

    Args:
        timeout: Maximum seconds allocated for broker and worker inspection.

    Returns:
        Structured health report containing overall status, broker diagnostics,
        and count/details of active worker nodes.
    """
    broker_health = check_redis_health(timeout=timeout)
    workers = list_active_workers(timeout=timeout)
    workers_online = len(workers)

    is_broker_ok = broker_health.get("status") == "healthy"
    overall_status = "healthy" if is_broker_ok and workers_online > 0 else "degraded"

    return {
        "status": overall_status,
        "timestamp": datetime.now(UTC).isoformat(),
        "broker": broker_health,
        "workers_online": workers_online,
        "workers": workers,
    }
