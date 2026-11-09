"""Worker failure detection and persistent heartbeat management."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.recovery import WorkerNodeModel

logger = logging.getLogger("app.services.recovery.failure_detector")


class WorkerFailureDetector:
    """Monitors worker heartbeats, detects unresponsive nodes, and tags offline workers."""

    def __init__(
        self,
        heartbeat_interval_seconds: int = 15,
        failure_threshold_seconds: int = 60,
        grace_period_seconds: int = 30,
    ) -> None:
        self.heartbeat_interval = heartbeat_interval_seconds
        self.failure_threshold = failure_threshold_seconds
        self.grace_period = grace_period_seconds

    async def record_heartbeat(
        self,
        worker_id: str,
        session: AsyncSession,
        hostname: str | None = None,
        queues: list[str] | None = None,
        active_tasks: list[dict[str, Any]] | None = None,
        worker_metadata: dict[str, Any] | None = None,
    ) -> WorkerNodeModel:
        """Upsert a worker node record with fresh heartbeat signal."""
        now = datetime.now(UTC)
        query = select(WorkerNodeModel).where(WorkerNodeModel.worker_id == worker_id)
        res = await session.execute(query)
        node = res.scalar_one_or_none()

        q_list = queues or ["default"]
        t_list = active_tasks or []
        m_dict = worker_metadata or {}

        if node is None:
            node = WorkerNodeModel(
                worker_id=worker_id,
                hostname=hostname,
                status="ONLINE",
                last_heartbeat=now,
                registered_queues=q_list,
                active_task_count=len(t_list),
                startup_time=now,
                worker_metadata=m_dict,
            )
            session.add(node)
        else:
            node.last_heartbeat = now
            node.status = "ONLINE"
            node.active_task_count = len(t_list)
            if hostname:
                node.hostname = hostname
            if queues:
                node.registered_queues = q_list
            if worker_metadata:
                node.worker_metadata.update(m_dict)

        await session.flush()
        return node

    async def detect_unhealthy_workers(
        self,
        session: AsyncSession,
    ) -> list[dict[str, Any]]:
        """Identify workers with expired heartbeats and transition status to SUSPECTED or OFFLINE.

        A worker is marked:
        - 'SUSPECTED': if missed heartbeat >= failure_threshold_seconds
        - 'OFFLINE': if missed heartbeat >= (failure_threshold_seconds + grace_period_seconds)
        """
        now = datetime.now(UTC)
        suspect_cutoff = now - timedelta(seconds=self.failure_threshold)
        offline_cutoff = now - timedelta(seconds=self.failure_threshold + self.grace_period)

        # 1. Transition SUSPECTED -> OFFLINE
        offline_stmt = (
            update(WorkerNodeModel)
            .where(
                WorkerNodeModel.status.in_(["ONLINE", "SUSPECTED"]),
                WorkerNodeModel.last_heartbeat < offline_cutoff,
            )
            .values(status="OFFLINE", shutdown_time=now)
        )
        await session.execute(offline_stmt)

        # 2. Transition ONLINE -> SUSPECTED
        suspect_stmt = (
            update(WorkerNodeModel)
            .where(
                WorkerNodeModel.status == "ONLINE",
                WorkerNodeModel.last_heartbeat < suspect_cutoff,
                WorkerNodeModel.last_heartbeat >= offline_cutoff,
            )
            .values(status="SUSPECTED")
        )
        await session.execute(suspect_stmt)
        await session.flush()

        # Query all non-online workers
        q = select(WorkerNodeModel).where(WorkerNodeModel.status.in_(["SUSPECTED", "OFFLINE"]))
        res = await session.execute(q)
        nodes = list(res.scalars().all())

        return [
            {
                "worker_id": n.worker_id,
                "status": n.status,
                "last_heartbeat": n.last_heartbeat.isoformat(),
                "seconds_since_heartbeat": round((now - n.last_heartbeat).total_seconds(), 1),
            }
            for n in nodes
        ]

    async def is_worker_healthy(
        self,
        worker_id: str,
        session: AsyncSession,
    ) -> bool:
        """Check whether a specific worker is actively reporting heartbeats."""
        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=self.failure_threshold + self.grace_period)
        query = select(WorkerNodeModel).where(
            WorkerNodeModel.worker_id == worker_id,
            WorkerNodeModel.status == "ONLINE",
            WorkerNodeModel.last_heartbeat >= cutoff,
        )
        res = await session.execute(query)
        return res.scalar_one_or_none() is not None
