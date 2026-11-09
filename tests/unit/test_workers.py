"""Unit tests for Celery distributed worker configuration, tasks, registry, and dispatcher."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.contracts import AgentExecutionStatus
from app.db.models.execution_plan import ExecutionPlanModel
from app.db.models.task import Task, TaskStatus
from app.db.models.workflow import Workflow, WorkflowStatus
from app.services.task_dispatcher import DistributedTaskDispatcher
from app.workers.celery_app import celery_app
from app.workers.health import get_worker_health_summary
from app.workers.redis_client import check_redis_health
from app.workers.tasks.execution_tasks import (
    execute_agent_task,
)
from app.workers.worker_registry import get_worker_stats, list_active_workers

# ==============================================================================
# Celery Configuration Validation Tests
# ==============================================================================


def test_celery_configuration_security_and_reliability():
    """Verify Celery app adheres to strict JSON serialization and reliability rules."""
    conf = celery_app.conf

    # Security: strictly JSON serialization, no pickle
    assert conf.task_serializer == "json"
    assert conf.result_serializer == "json"
    assert conf.accept_content == ["json"]

    # Reliability: Late ACKs and fair prefetch
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.worker_prefetch_multiplier == 1

    # Timeouts
    assert conf.task_time_limit == 600
    assert conf.task_soft_time_limit == 540


def test_celery_queues_and_routes():
    """Verify configured logical queues and designated task routes."""
    conf = celery_app.conf
    queue_names = {q.name for q in conf.task_queues}
    expected_queues = {"default", "research", "analysis", "coding"}
    assert expected_queues.issubset(queue_names)

    routes = conf.task_routes
    assert routes["app.workers.tasks.execution_tasks.execute_research_task"]["queue"] == "research"
    assert routes["app.workers.tasks.execution_tasks.execute_analysis_task"]["queue"] == "analysis"
    assert routes["app.workers.tasks.execution_tasks.execute_coding_task"]["queue"] == "coding"
    assert routes["app.workers.tasks.execution_tasks.execute_agent_task"]["queue"] == "default"


# ==============================================================================
# Redis Client & Health Probe Tests
# ==============================================================================


def test_check_redis_health_success():
    """Verify check_redis_health reports healthy when Redis responds to ping."""
    with patch("app.workers.redis_client.get_redis_connection") as mock_conn_fn:
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.info.side_effect = lambda section: (
            {"redis_version": "7.2.4"} if section == "server" else {"connected_clients": 5}
        )
        mock_conn_fn.return_value = mock_client

        health = check_redis_health()
        assert health["status"] == "healthy"
        assert health["redis_version"] == "7.2.4"
        assert health["connected_clients"] == 5
        mock_client.close.assert_called_once()


def test_check_redis_health_failure():
    """Verify check_redis_health reports unreachable without raising exception on error."""
    with patch("app.workers.redis_client.get_redis_connection") as mock_conn_fn:
        mock_client = MagicMock()
        mock_client.ping.side_effect = ConnectionError("Could not connect to Redis broker")
        mock_conn_fn.return_value = mock_client

        health = check_redis_health()
        assert health["status"] == "unreachable"
        assert "Could not connect" in health["error"]


# ==============================================================================
# Worker Registry & Health Diagnostics Tests
# ==============================================================================


def test_list_active_workers_with_running_nodes():
    """Verify list_active_workers returns structured information for active nodes."""
    with patch.object(celery_app.control, "inspect") as mock_inspect_fn:
        mock_inspector = MagicMock()
        mock_inspector.ping.return_value = {"celery@worker-node-1": {"ok": "pong"}}
        mock_inspector.active_queues.return_value = {
            "celery@worker-node-1": [{"name": "default"}, {"name": "research"}]
        }
        mock_inspector.active.return_value = {
            "celery@worker-node-1": [{"id": "task-uuid-1", "name": "execute_research_task"}]
        }
        mock_inspector.stats.return_value = {
            "celery@worker-node-1": {"pool": {"implementation": "solo", "max-concurrency": 1}}
        }
        mock_inspect_fn.return_value = mock_inspector

        workers = list_active_workers()
        assert len(workers) == 1
        w = workers[0]
        assert w["worker_name"] == "celery@worker-node-1"
        assert w["status"] == "online"
        assert w["queues"] == ["default", "research"]
        assert w["active_tasks_count"] == 1
        assert w["pool_type"] == "solo"


def test_list_active_workers_offline_or_error():
    """Verify list_active_workers returns empty list when Celery broker is offline."""
    with patch.object(celery_app.control, "inspect") as mock_inspect_fn:
        mock_inspect_fn.side_effect = Exception("Broker connection timed out")
        workers = list_active_workers()
        assert workers == []


def test_get_worker_stats():
    """Verify get_worker_stats retrieves stats dict gracefully."""
    with patch.object(celery_app.control, "inspect") as mock_inspect_fn:
        mock_inspector = MagicMock()
        mock_inspector.stats.return_value = {"node-1": {"uptime": 3600}}
        mock_inspect_fn.return_value = mock_inspector

        all_stats = get_worker_stats()
        assert "node-1" in all_stats

        node_stats = get_worker_stats(worker_name="node-1")
        assert node_stats == {"uptime": 3600}


def test_get_worker_health_summary():
    """Verify get_worker_health_summary produces correct composite status."""
    with (
        patch("app.workers.health.check_redis_health") as mock_redis_h,
        patch("app.workers.health.list_active_workers") as mock_workers_fn,
    ):
        # Case 1: Healthy broker + online workers
        mock_redis_h.return_value = {"status": "healthy", "redis_version": "7.2.4"}
        mock_workers_fn.return_value = [{"worker_name": "worker-1", "status": "online"}]

        res = get_worker_health_summary()
        assert res["status"] == "healthy"
        assert res["workers_online"] == 1

        # Case 2: Healthy broker + 0 workers -> degraded
        mock_workers_fn.return_value = []
        res = get_worker_health_summary()
        assert res["status"] == "degraded"
        assert res["workers_online"] == 0

        # Case 3: Unreachable broker -> degraded
        mock_redis_h.return_value = {"status": "unreachable", "error": "refused"}
        res = get_worker_health_summary()
        assert res["status"] == "degraded"


# ==============================================================================
# Worker Task Execution & Idempotency Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_worker_task_idempotency_skip():
    """Verify execute_agent_task returns immediately if task is already SUCCEEDED."""
    task_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    plan_id = uuid.uuid4()

    mock_task = MagicMock(spec=Task)
    mock_task.id = task_id
    mock_task.status = TaskStatus.SUCCEEDED.value

    mock_session = AsyncMock(spec=AsyncSession)
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_task
    mock_session.execute = AsyncMock(return_value=mock_result)

    with patch("app.workers.tasks.execution_tasks.get_worker_session") as mock_session_ctx:
        mock_session_ctx.return_value.__aenter__.return_value = mock_session

        res = execute_agent_task.run(
            workflow_id=str(workflow_id),
            plan_id=str(plan_id),
            planned_task_id="task_01",
            task_record_id=str(task_id),
        )

        assert res["status"] == TaskStatus.SUCCEEDED.value
        assert res["task_id"] == str(task_id)
        assert res["message"] == "Already completed"


@pytest.mark.asyncio
async def test_worker_task_successful_execution():
    """Verify execute_agent_task claims task, executes agent, updates DB, and advances stage."""
    task_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    plan_id = uuid.uuid4()

    mock_task = MagicMock(spec=Task)
    mock_task.id = task_id
    mock_task.status = TaskStatus.QUEUED.value
    mock_task.task_type = "research"
    mock_task.name = "Conduct literature search"
    mock_task.description = "Deep research into distributed systems"
    mock_task.input_data = {"user_request": "Investigate vector clocks"}
    mock_task.retry_count = 0

    mock_plan = MagicMock(spec=ExecutionPlanModel)
    mock_plan.id = plan_id
    mock_plan.objective = "Investigate vector clocks"
    mock_plan.tasks_data = [
        {
            "task_id": "task_01",
            "name": "Conduct literature search",
            "description": "Deep research into distributed systems",
            "recommended_agent_id": "researcher",
        }
    ]

    mock_workflow = MagicMock(spec=Workflow)
    mock_workflow.id = workflow_id
    mock_workflow.status = WorkflowStatus.PENDING.value

    mock_session = AsyncMock(spec=AsyncSession)

    def mock_execute_side_effect(query):
        mock_result = MagicMock()
        mock_result.rowcount = 1
        if hasattr(query, "column_descriptions") and query.column_descriptions:
            entity = query.column_descriptions[0]["entity"]
            if entity == Task:
                mock_result.scalar_one_or_none.return_value = mock_task
            elif entity == ExecutionPlanModel:
                mock_result.scalar_one_or_none.return_value = mock_plan
            elif entity == Workflow:
                mock_result.scalar_one_or_none.return_value = mock_workflow
        return mock_result

    mock_session.execute.side_effect = mock_execute_side_effect

    mock_agent_result = MagicMock()
    mock_agent_result.status = AgentExecutionStatus.SUCCESS
    mock_agent_result.result = {"findings": "Vector clocks ensure causal consistency"}
    mock_agent_result.error = None

    with (
        patch("app.workers.tasks.execution_tasks.get_worker_session") as mock_session_ctx,
        patch("app.workers.tasks.execution_tasks.get_agent_execution_service") as mock_get_exec_svc,
        patch("app.services.task_dispatcher.DistributedTaskDispatcher") as mock_dispatcher_cls,
    ):
        mock_session_ctx.return_value.__aenter__.return_value = mock_session
        mock_exec_instance = MagicMock()
        mock_exec_instance.execute_agent = AsyncMock(return_value=mock_agent_result)
        mock_get_exec_svc.return_value = mock_exec_instance

        mock_dispatcher_instance = mock_dispatcher_cls.return_value
        mock_dispatcher_instance.advance_workflow_stages = AsyncMock(return_value=["task_02"])

        res = execute_agent_task.run(
            workflow_id=str(workflow_id),
            plan_id=str(plan_id),
            planned_task_id="task_01",
            task_record_id=str(task_id),
        )

        assert res["status"] == TaskStatus.SUCCEEDED.value
        assert res["planned_task_id"] == "task_01"
        assert res["task_id"] == str(task_id)
        assert mock_task.status == TaskStatus.SUCCEEDED.value
        assert mock_task.result_data == {"findings": "Vector clocks ensure causal consistency"}


# ==============================================================================
# DistributedTaskDispatcher Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_dispatcher_dispatch_plan_stage1():
    """Verify DistributedTaskDispatcher enqueues stage 1 tasks to Celery."""
    plan_id = uuid.uuid4()
    workflow_id = uuid.uuid4()

    mock_plan = MagicMock(spec=ExecutionPlanModel)
    mock_plan.id = plan_id
    mock_plan.workflow_id = workflow_id
    mock_plan.objective = "Build distributed search"
    mock_plan.stages_data = [
        {
            "stage_number": 1,
            "tasks": [
                {
                    "task_id": "task_01",
                    "name": "Literature Research",
                    "description": "Research paper indexing",
                    "task_type": "research",
                    "dependencies": [],
                }
            ],
        },
        {
            "stage_number": 2,
            "tasks": [
                {
                    "task_id": "task_02",
                    "name": "Implement indexing code",
                    "description": "Code indexing engine",
                    "task_type": "coding",
                    "dependencies": ["task_01"],
                }
            ],
        },
    ]

    mock_workflow = MagicMock(spec=Workflow)
    mock_workflow.id = workflow_id
    mock_workflow.status = WorkflowStatus.PENDING.value

    mock_session = AsyncMock(spec=AsyncSession)

    def mock_execute_side_effect(query):
        mock_result = MagicMock()
        entity = query.column_descriptions[0]["entity"]
        if entity == ExecutionPlanModel:
            mock_result.scalar_one_or_none.return_value = mock_plan
        elif entity == Workflow:
            mock_result.scalar_one_or_none.return_value = mock_workflow
        elif entity == Task:
            mock_result.scalar_one_or_none.return_value = None
        return mock_result

    mock_session.execute.side_effect = mock_execute_side_effect

    dispatcher = DistributedTaskDispatcher()

    with patch("app.workers.tasks.execution_tasks.execute_research_task.apply_async") as mock_apply:
        mock_apply.return_value = MagicMock(id="celery-job-123")

        result = await dispatcher.dispatch_plan(plan_id=plan_id, session=mock_session)

        assert result["workflow_id"] == str(workflow_id)
        assert result["plan_id"] == str(plan_id)
        assert result["dispatched_stage"] == 1
        assert result["dispatched_tasks_count"] == 1
        assert result["dispatched_tasks"][0]["planned_task_id"] == "task_01"
        assert result["dispatched_tasks"][0]["queue"] == "research"
        assert result["dispatched_tasks"][0]["celery_task_id"] == "celery-job-123"


@pytest.mark.asyncio
async def test_dispatcher_advance_workflow_stages_unblocking():
    """Verify advance_workflow_stages dispatches stage 2 once stage 1 is completed."""
    workflow_id = uuid.uuid4()
    plan_id = uuid.uuid4()

    # Stage 1 task is SUCCEEDED
    task_1 = MagicMock(spec=Task)
    task_1.workflow_id = workflow_id
    task_1.plan_id = plan_id
    task_1.planned_task_id = "task_01"
    task_1.status = TaskStatus.SUCCEEDED.value

    mock_plan = MagicMock(spec=ExecutionPlanModel)
    mock_plan.id = plan_id
    mock_plan.stages_data = [
        {"stage_number": 1, "tasks": [{"task_id": "task_01", "dependencies": []}]},
        {
            "stage_number": 2,
            "tasks": [
                {
                    "task_id": "task_02",
                    "name": "Code implementation",
                    "description": "Implement feature",
                    "task_type": "coding",
                    "dependencies": ["task_01"],
                }
            ],
        },
    ]

    mock_workflow = MagicMock(spec=Workflow)
    mock_workflow.id = workflow_id
    mock_workflow.status = WorkflowStatus.RUNNING.value

    mock_session = AsyncMock(spec=AsyncSession)

    def mock_execute_side_effect(query):
        mock_result = MagicMock()
        entity = query.column_descriptions[0]["entity"]
        if entity == Task:
            mock_result.scalars.return_value.all.return_value = [task_1]
        elif entity == ExecutionPlanModel:
            mock_result.scalar_one_or_none.return_value = mock_plan
        elif entity == Workflow:
            mock_result.scalar_one_or_none.return_value = mock_workflow
        return mock_result

    mock_session.execute.side_effect = mock_execute_side_effect

    dispatcher = DistributedTaskDispatcher()

    with patch("app.workers.tasks.execution_tasks.execute_coding_task.apply_async") as mock_apply:
        mock_apply.return_value = MagicMock(id="celery-job-456")

        newly_dispatched = await dispatcher.advance_workflow_stages(
            workflow_id=workflow_id, session=mock_session
        )

        assert newly_dispatched == ["task_02"]
        mock_apply.assert_called_once()
