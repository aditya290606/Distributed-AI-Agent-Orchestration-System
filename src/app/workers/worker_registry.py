"""Worker registry utilities querying Celery control APIs for worker discovery and statistics."""

import logging
from datetime import UTC, datetime
from typing import Any

from app.workers.celery_app import celery_app

logger = logging.getLogger("app.workers.registry")


def list_active_workers(timeout: float = 1.0) -> list[dict[str, Any]]:
    """Query Celery inspection APIs to discover currently connected workers and their status.

    Args:
        timeout: Maximum seconds to wait for worker responses across the broker.

    Returns:
        List of dictionaries with worker status, registered queues, and active workloads.
        Returns an empty list gracefully if the broker is unreachable or no workers respond.
    """
    try:
        inspector = celery_app.control.inspect(timeout=timeout)
        if inspector is None:
            return []

        # Execute ping first to find reachable worker nodes
        pings = inspector.ping()
        if not pings:
            return []

        active_queues_map = inspector.active_queues() or {}
        active_tasks_map = inspector.active() or {}
        stats_map = inspector.stats() or {}

        workers: list[dict[str, Any]] = []
        for worker_name, ping_resp in pings.items():
            # ping_resp is typically {'ok': 'pong'}
            is_pong = isinstance(ping_resp, dict) and ping_resp.get("ok") == "pong"
            queues_info = active_queues_map.get(worker_name, [])
            queues = (
                [q.get("name") for q in queues_info if isinstance(q, dict)] if queues_info else []
            )

            worker_stats = stats_map.get(worker_name, {})
            pool_info = worker_stats.get("pool", {}) if isinstance(worker_stats, dict) else {}
            concurrency = pool_info.get("max-concurrency", 1) if isinstance(pool_info, dict) else 1

            active_tasks = active_tasks_map.get(worker_name, [])
            current_tasks = [
                {
                    "task_id": t.get("id"),
                    "name": t.get("name"),
                    "args": t.get("args"),
                    "time_start": t.get("time_start"),
                }
                for t in active_tasks
                if isinstance(t, dict)
            ]

            workers.append(
                {
                    "worker_name": worker_name,
                    "status": "online" if is_pong else "unknown",
                    "active_tasks_count": len(current_tasks),
                    "active_tasks": current_tasks,
                    "queues": queues,
                    "concurrency": concurrency,
                    "pool_type": pool_info.get("implementation", "unknown")
                    if isinstance(pool_info, dict)
                    else "unknown",
                    "last_seen": datetime.now(UTC).isoformat(),
                }
            )

        return workers
    except Exception as e:
        logger.warning("Failed to inspect Celery workers via broker: %s", e)
        return []


def get_worker_stats(worker_name: str | None = None, timeout: float = 1.0) -> dict[str, Any]:
    """Retrieve detailed runtime statistics for all or a specific Celery worker node.

    Args:
        worker_name: Optional target worker node name.
        timeout: Maximum seconds to wait for worker responses.

    Returns:
        Dictionary of worker statistics, or empty dict if unreachable.
    """
    try:
        inspector = celery_app.control.inspect(timeout=timeout)
        if inspector is None:
            return {}

        stats = inspector.stats() or {}
        if worker_name:
            return stats.get(worker_name, {})
        return stats
    except Exception as e:
        logger.warning("Failed to retrieve Celery worker stats: %s", e)
        return {}
