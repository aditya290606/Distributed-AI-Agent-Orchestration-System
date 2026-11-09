"""Centralized Celery application initialization and task routing configuration."""

import logging

from celery import Celery
from kombu import Exchange, Queue

from app.core.config import get_settings

logger = logging.getLogger("app.workers.celery")

settings = get_settings()

# Initialize Celery Application
celery_app = Celery("nexus_ai")

# Define Logical Queues for Agent Workloads
default_exchange = Exchange("nexus_tasks", type="direct")

CELERY_QUEUES = (
    Queue("default", default_exchange, routing_key="task.default"),
    Queue("research", default_exchange, routing_key="task.research"),
    Queue("analysis", default_exchange, routing_key="task.analysis"),
    Queue("coding", default_exchange, routing_key="task.coding"),
)

# Celery Configuration Dictionary
celery_app.conf.update(
    broker_url=settings.resolved_celery_broker_url,
    result_backend=settings.resolved_celery_result_backend,
    # Serialization Security: Strictly JSON only, no unsafe pickle
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Reliability: Acknowledge task only AFTER completion so crashed tasks are re-queued
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Fair worker dispatch: Single task prefetch per worker process
    worker_prefetch_multiplier=1,
    # Execution Timeouts: 9 minutes soft warning, 10 minutes hard termination
    task_time_limit=600,
    task_soft_time_limit=540,
    # Result TTL: 24 hours in Redis (MySQL is durable source of truth)
    result_expires=86400,
    # Test suite support
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=True,
    # Queue Definitions
    task_queues=CELERY_QUEUES,
    task_default_queue="default",
    task_default_exchange="nexus_tasks",
    task_default_routing_key="task.default",
    # Task Routes
    task_routes={
        "app.workers.tasks.execution_tasks.execute_research_task": {"queue": "research"},
        "app.workers.tasks.execution_tasks.execute_analysis_task": {"queue": "analysis"},
        "app.workers.tasks.execution_tasks.execute_coding_task": {"queue": "coding"},
        "app.workers.tasks.execution_tasks.execute_agent_task": {"queue": "default"},
        "app.workers.scheduler.recover_orphaned_tasks": {"queue": "default"},
        "app.workers.scheduler.detect_worker_heartbeat_failures": {"queue": "default"},
    },
    # Periodic Recovery Scheduling (Celery Beat)
    beat_schedule={
        "recover-orphaned-tasks-every-30s": {
            "task": "app.workers.scheduler.recover_orphaned_tasks",
            "schedule": 30.0,
        },
        "detect-worker-failures-every-30s": {
            "task": "app.workers.scheduler.detect_worker_heartbeat_failures",
            "schedule": 30.0,
        },
    },
)

# Autodiscover worker tasks from app.workers.tasks and app.workers
celery_app.autodiscover_tasks(["app.workers.tasks", "app.workers"])

logger.info(
    "Celery application initialized (broker: %s, eager: %s)",
    settings.resolved_celery_broker_url,
    settings.celery_task_always_eager,
)
