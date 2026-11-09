"""Automated periodic recovery scheduler tasks coordinating self-healing scans."""

import logging
from typing import Any

from app.workers.celery_app import celery_app
from app.workers.tasks.execution_tasks import _run_async_in_worker, get_worker_session

logger = logging.getLogger("app.workers.scheduler")


async def _async_recover_orphaned_tasks() -> list[dict[str, Any]]:
    """Execute asynchronous task recovery scan within isolated database session."""
    from app.services.recovery.task_recovery_manager import TaskRecoveryManager

    async with get_worker_session() as session:
        manager = TaskRecoveryManager()
        outcomes = await manager.scan_and_recover_tasks(session=session)
        logger.info("Automated recovery scan completed. %d tasks processed.", len(outcomes))
        return outcomes


async def _async_detect_worker_failures() -> list[dict[str, Any]]:
    """Detect unresponsive workers and update health status in database."""
    from app.services.recovery.failure_detector import WorkerFailureDetector

    async with get_worker_session() as session:
        detector = WorkerFailureDetector()
        unhealthy = await detector.detect_unhealthy_workers(session=session)
        if unhealthy:
            logger.warning("Worker failure scan detected %d unhealthy nodes.", len(unhealthy))
        return unhealthy


@celery_app.task(name="app.workers.scheduler.recover_orphaned_tasks")
def recover_orphaned_tasks() -> list[dict[str, Any]]:
    """Periodic task scanning for expired leases, stuck RUNNING tasks, and dead worker tasks."""
    return _run_async_in_worker(_async_recover_orphaned_tasks())


@celery_app.task(name="app.workers.scheduler.detect_worker_heartbeat_failures")
def detect_worker_heartbeat_failures() -> list[dict[str, Any]]:
    """Periodic task evaluating worker heartbeats and marking dead/suspected nodes."""
    return _run_async_in_worker(_async_detect_worker_failures())
