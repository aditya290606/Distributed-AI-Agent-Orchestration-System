"""End-to-End Fault-Injection and Self-Healing Recovery Tests.

Validates all 6 core recovery scenarios:
1. Scenario A: Worker crashes during execution (lease expires -> detected -> retried/requeued).
2. Scenario B: Duplicate Celery delivery (idempotency guard preserves output, no duplicate execution).
3. Scenario C: Redis broker interruption (graceful degradation, unblocked recovery, no false completion).
4. Scenario D: Temporary Gemini API failure (circuit breaker: CLOSED -> OPEN -> HALF_OPEN -> CLOSED).
5. Scenario E: Application restarts during workflow (resumption preserves completed stage deliverables).
6. Scenario F: Prerequisite task fails permanently (dependent tasks marked blocked, independent results kept).
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.execution_plan import ExecutionPlanModel
from app.db.models.recovery import RecoveryEventModel
from app.db.models.task import Task, TaskStatus
from app.db.models.workflow import Workflow, WorkflowStatus
from app.services.llm.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
)
from app.services.recovery.retry_policy import ErrorCategory, RetryPolicy
from app.services.recovery.task_recovery_manager import TaskRecoveryManager
from app.services.recovery.workflow_recovery import WorkflowRecoveryService
from app.workers.redis_client import check_redis_health
from app.workers.tasks.execution_tasks import execute_agent_task

# ==============================================================================
# Scenario A: Worker Crash During Execution (Lease Expiration & Requeue)
# ==============================================================================


@pytest.mark.asyncio
async def test_fault_scenario_a_worker_crash_lease_expiration_and_recovery():
    """Scenario A: Worker crashes mid-execution. Lease expires, is detected, and task is requeued."""
    task_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    now = datetime.now(UTC)

    # 1. Simulating a crashed worker that acquired a lease 10 minutes ago with a 5-minute timeout
    crashed_task = Task(
        id=task_id,
        workflow_id=workflow_id,
        plan_id=plan_id,
        planned_task_id="task_crash_01",
        name="Literature extraction",
        task_type="research",
        status=TaskStatus.RUNNING.value,
        worker_id="celery@worker-crashed-pid-9999",
        lease_token="lease-token-crashed-worker",
        lease_acquired_at=now - timedelta(minutes=10),
        lease_expires_at=now - timedelta(minutes=5),  # EXPIRED!
        recovery_count=0,
        retry_count=0,
    )

    mock_session = AsyncMock(spec=AsyncSession)

    def mock_exec_side_effect(q):
        m = MagicMock()
        q_str = str(q)
        if "WHERE tasks.id =" in q_str:
            m.scalar_one_or_none.return_value = crashed_task
        else:
            m.scalars.return_value.all.return_value = [crashed_task]
        return m

    mock_session.execute.side_effect = mock_exec_side_effect

    # 2. Recovery manager executes periodic scan or targeted recovery
    recovery_mgr = TaskRecoveryManager(max_recovery_attempts=3)

    with patch("app.workers.tasks.execution_tasks.execute_research_task.apply_async") as mock_apply:
        mock_apply.return_value = MagicMock(id="new-celery-job-recovered")

        recovery_outcome = await recovery_mgr.recover_task(
            task_id=task_id,
            session=mock_session,
            reason="LEASE_EXPIRED_WORKER_CRASHED",
        )

        # 3. Assert task was reclaimed, lease revoked, and re-dispatched to Celery
        assert recovery_outcome is not None
        assert recovery_outcome["decision"] == "REQUEUE"
        assert recovery_outcome["recovery_count"] == 1
        assert recovery_outcome["celery_task_id"] == "new-celery-job-recovered"

        assert crashed_task.status == TaskStatus.QUEUED.value
        assert crashed_task.lease_token is None
        assert crashed_task.lease_expires_at is None
        assert crashed_task.last_error_category == ErrorCategory.WORKER_FAILURE.value

        # 4. Verify immutable recovery event was staged for persistence
        mock_session.add.assert_called_once()
        added_event = mock_session.add.call_args[0][0]
        assert isinstance(added_event, RecoveryEventModel)
        assert added_event.recovery_action == "REQUEUE"
        assert added_event.previous_status == TaskStatus.RUNNING.value
        assert added_event.new_status == TaskStatus.QUEUED.value
        assert added_event.worker_id == "celery@worker-crashed-pid-9999"


# ==============================================================================
# Scenario B: Duplicate Celery Message Delivery (Idempotency Guard)
# ==============================================================================


@pytest.mark.asyncio
async def test_fault_scenario_b_duplicate_celery_message_idempotency_guard():
    """Scenario B: Duplicate Celery message arrives after task already completed. Output preserved."""
    task_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    plan_id = uuid.uuid4()

    # Task is already in SUCCEEDED state with validated deliverables
    existing_output = {"findings": "Verified causal consistency in distributed graph"}
    completed_task = MagicMock(spec=Task)
    completed_task.id = task_id
    completed_task.status = TaskStatus.SUCCEEDED.value
    completed_task.output_data = existing_output

    mock_session = AsyncMock(spec=AsyncSession)
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = completed_task
    mock_session.execute.return_value = mock_res

    # Simulate Celery worker invoking task on duplicate delivery
    with (
        patch("app.workers.tasks.execution_tasks.get_worker_session") as mock_ctx,
        patch("app.workers.tasks.execution_tasks.get_agent_execution_service") as mock_agent_svc,
        patch("app.services.task_dispatcher.DistributedTaskDispatcher") as mock_dispatcher,
    ):
        mock_ctx.return_value.__aenter__.return_value = mock_session
        mock_exec_instance = MagicMock()
        mock_agent_svc.return_value = mock_exec_instance

        res = execute_agent_task.run(
            workflow_id=str(workflow_id),
            plan_id=str(plan_id),
            planned_task_id="task_dup_01",
            task_record_id=str(task_id),
        )

        # Assert worker exited immediately without re-executing agent
        assert res["status"] == TaskStatus.SUCCEEDED.value
        assert res["message"] == "Already completed"
        assert res["task_id"] == str(task_id)

        # Critical: Agent execution must NEVER have been called on duplicate
        mock_exec_instance.execute_agent.assert_not_called()
        mock_dispatcher.assert_not_called()
        assert completed_task.output_data == existing_output


# ==============================================================================
# Scenario C: Redis Broker Interruption (Graceful Degradation)
# ==============================================================================


@pytest.mark.asyncio
async def test_fault_scenario_c_redis_broker_interruption_graceful_handling():
    """Scenario C: Redis broker is temporarily unreachable. Fails gracefully without data corruption."""
    # 1. Health check handles Redis ConnectionError gracefully
    with patch("app.workers.redis_client.get_redis_connection") as mock_conn:
        mock_redis = MagicMock()
        mock_redis.ping.side_effect = ConnectionError("Redis server connection refused")
        mock_conn.return_value = mock_redis

        health = check_redis_health()
        assert health["status"] == "unreachable"
        assert "Redis server connection refused" in health["error"]

    # 2. Celery dispatch failure does not corrupt database state
    from app.services.task_dispatcher import DistributedTaskDispatcher

    dispatcher = DistributedTaskDispatcher()
    workflow_id = uuid.uuid4()
    plan_id = uuid.uuid4()

    mock_task_spec = {
        "task_id": "task_01",
        "name": "Research topic",
        "task_type": "research",
        "description": "Deep research",
    }

    mock_wf = MagicMock(spec=Workflow)
    mock_wf.id = workflow_id
    mock_wf.status = WorkflowStatus.RUNNING.value

    mock_plan = MagicMock(spec=ExecutionPlanModel)
    mock_plan.id = plan_id
    mock_plan.stages_data = [{"stage_number": 1, "tasks": [mock_task_spec]}]

    mock_session = AsyncMock(spec=AsyncSession)

    def mock_exec_side_effect(q):
        m = MagicMock()
        q_str = str(q)
        if "workflows" in q_str:
            m.scalar_one_or_none.return_value = mock_wf
        elif "execution_plans" in q_str:
            m.scalar_one_or_none.return_value = mock_plan
        else:
            m.scalars.return_value.all.return_value = []
        return m

    mock_session.execute.side_effect = mock_exec_side_effect

    # Broker apply_async fails with connection error: dispatcher handles it gracefully
    with patch("app.workers.tasks.execution_tasks.execute_research_task.apply_async") as mock_apply:
        mock_apply.side_effect = ConnectionError("Broker unreachable")

        dispatch_res = await dispatcher.dispatch_plan(
            plan_id=plan_id,
            session=mock_session,
        )

        # Dispatcher caught broker error gracefully and did not crash
        assert dispatch_res["dispatched_tasks_count"] == 0
        assert mock_wf.status == WorkflowStatus.RUNNING.value


# ==============================================================================
# Scenario D: Temporary Gemini Failure & Circuit Breaker State Transitions
# ==============================================================================


@pytest.mark.asyncio
async def test_fault_scenario_d_gemini_circuit_breaker_and_backoff():
    """Scenario D: Repeated LLM errors trip breaker OPEN; fast-fails; probes in HALF_OPEN; resets CLOSED."""
    breaker = CircuitBreaker(
        failure_threshold=3, recovery_timeout_seconds=0.1, half_open_success_threshold=2
    )

    # 1. Initially CLOSED
    assert breaker.state == CircuitState.CLOSED

    # 2. Simulate 3 consecutive LLM rate-limit/timeout errors
    async def _failing_gemini_call():
        raise TimeoutError("504 Gateway Timeout: Gemini API not responding")

    for _ in range(3):
        with pytest.raises(TimeoutError):
            await breaker.execute(_failing_gemini_call)

    # 3. Breaker is now OPEN
    assert breaker.state == CircuitState.OPEN

    # 4. Subsequent calls fast-fail without touching Gemini API
    with pytest.raises(CircuitBreakerOpenError) as exc_info:
        await breaker.execute(AsyncMock())
    assert "circuit breaker is open" in str(exc_info.value).lower()

    # 5. After recovery timeout, transitions to HALF_OPEN
    await asyncio.sleep(0.12)
    assert breaker.state == CircuitState.HALF_OPEN

    # 6. First probe succeeds -> stays HALF_OPEN
    await breaker.record_success()
    assert breaker.state == CircuitState.HALF_OPEN

    # 7. Second probe succeeds -> resets to CLOSED!
    await breaker.record_success()
    assert breaker.state == CircuitState.CLOSED

    # 8. Retry policy computes bounded exponential backoff
    retry_policy = RetryPolicy(
        initial_delay_seconds=1.0, max_delay_seconds=10.0, enable_jitter=False
    )
    dec0 = retry_policy.evaluate(TimeoutError("Timeout"), current_retry_count=0)
    assert dec0.should_retry is True
    assert dec0.delay_seconds == 1.0

    dec1 = retry_policy.evaluate(TimeoutError("Timeout"), current_retry_count=1)
    assert dec1.delay_seconds == 2.0

    dec2 = retry_policy.evaluate(TimeoutError("Timeout"), current_retry_count=2)
    assert dec2.delay_seconds == 4.0


# ==============================================================================
# Scenario E: Application Restart During Workflow (Stateful Resumption)
# ==============================================================================


@pytest.mark.asyncio
async def test_fault_scenario_e_app_restart_preserves_deliverables_and_resumes():
    """Scenario E: Application restarts mid-workflow. Stage 1 deliverables preserved; Stage 2 resumed."""
    workflow_id = uuid.uuid4()
    plan_id = uuid.uuid4()

    mock_wf = MagicMock(spec=Workflow)
    mock_wf.id = workflow_id
    mock_wf.status = WorkflowStatus.RUNNING.value
    mock_wf.input_data = {"plan_id": str(plan_id)}

    # Stage 1: Already succeeded prior to restart
    task_1 = MagicMock(spec=Task)
    task_1.id = uuid.uuid4()
    task_1.workflow_id = workflow_id
    task_1.plan_id = plan_id
    task_1.planned_task_id = "task_01"
    task_1.status = TaskStatus.SUCCEEDED.value
    task_1.output_data = {"summary": "Architecture audit complete"}

    # Stage 2: Was in-flight when app killed (lease expired, unblocked)
    task_2 = MagicMock(spec=Task)
    task_2.id = uuid.uuid4()
    task_2.workflow_id = workflow_id
    task_2.plan_id = plan_id
    task_2.planned_task_id = "task_02"
    task_2.task_type = "coding"
    task_2.status = TaskStatus.RUNNING.value

    # Plan with 2 stages
    mock_plan = MagicMock(spec=ExecutionPlanModel)
    mock_plan.id = plan_id
    mock_plan.stages_data = [
        {"stage_number": 1, "tasks": [{"task_id": "task_01", "dependencies": []}]},
        {
            "stage_number": 2,
            "tasks": [
                {
                    "task_id": "task_02",
                    "name": "Backend coding",
                    "task_type": "coding",
                    "dependencies": ["task_01"],
                }
            ],
        },
    ]

    mock_session = AsyncMock(spec=AsyncSession)

    def mock_exec_side_effect(q):
        m = MagicMock()
        q_str = str(q)
        if "workflows" in q_str:
            m.scalar_one_or_none.return_value = mock_wf
        elif "execution_plans" in q_str:
            m.scalar_one_or_none.return_value = mock_plan
        else:
            m.scalars.return_value.all.return_value = [task_1, task_2]
        return m

    mock_session.execute.side_effect = mock_exec_side_effect

    svc = WorkflowRecoveryService()

    with patch("app.workers.tasks.execution_tasks.execute_coding_task.apply_async") as mock_apply:
        mock_apply.return_value = MagicMock(id="new-celery-stage2-job")

        result = await svc.resume_workflow(workflow_id=workflow_id, session=mock_session)

        # Assert Stage 1 completed deliverable is preserved
        assert result["preserved_tasks_count"] == 1
        assert "task_01" in result["preserved_tasks"]
        assert task_1.output_data["summary"] == "Architecture audit complete"
        assert task_1.status == TaskStatus.SUCCEEDED.value

        # Assert Stage 2 task was re-queued and dispatched
        assert result["resumed_tasks_count"] == 1
        assert "task_02" in result["resumed_tasks"]
        assert task_2.status == TaskStatus.QUEUED.value
        mock_apply.assert_called_once()


# ==============================================================================
# Scenario F: Prerequisite Task Permanent Failure (Dependent Blocking)
# ==============================================================================


@pytest.mark.asyncio
async def test_fault_scenario_f_permanent_task_failure_blocks_dependents_preserves_others():
    """Scenario F: Task fails permanently. Dependents blocked; independent tasks & prior results intact."""
    workflow_id = uuid.uuid4()
    plan_id = uuid.uuid4()

    mock_wf = MagicMock(spec=Workflow)
    mock_wf.id = workflow_id
    mock_wf.status = WorkflowStatus.RUNNING.value
    mock_wf.input_data = {"plan_id": str(plan_id)}

    # Task 1A: Succeeded independent task
    task_1a = MagicMock(spec=Task)
    task_1a.id = uuid.uuid4()
    task_1a.workflow_id = workflow_id
    task_1a.plan_id = plan_id
    task_1a.planned_task_id = "task_01_a"
    task_1a.status = TaskStatus.SUCCEEDED.value
    task_1a.output_data = {"findings": "Independent analysis ok"}

    # Task 1B: Permanently failed prerequisite
    task_1b = MagicMock(spec=Task)
    task_1b.id = uuid.uuid4()
    task_1b.workflow_id = workflow_id
    task_1b.plan_id = plan_id
    task_1b.planned_task_id = "task_01_b"
    task_1b.status = TaskStatus.FAILED.value
    task_1b.error_message = "Recovery attempts exhausted: Permanent hardware fault"

    # Task 2: Dependent on task_01_b (should be BLOCKED)
    task_2 = MagicMock(spec=Task)
    task_2.id = uuid.uuid4()
    task_2.workflow_id = workflow_id
    task_2.plan_id = plan_id
    task_2.planned_task_id = "task_02"
    task_2.status = TaskStatus.PENDING.value

    mock_plan = MagicMock(spec=ExecutionPlanModel)
    mock_plan.id = plan_id
    mock_plan.stages_data = [
        {
            "stage_number": 1,
            "tasks": [
                {"task_id": "task_01_a", "dependencies": []},
                {"task_id": "task_01_b", "dependencies": []},
            ],
        },
        {
            "stage_number": 2,
            "tasks": [
                {
                    "task_id": "task_02",
                    "name": "Dependent aggregation",
                    "task_type": "analysis",
                    "dependencies": ["task_01_b"],
                }
            ],
        },
    ]

    mock_session = AsyncMock(spec=AsyncSession)

    def mock_exec_side_effect(q):
        m = MagicMock()
        q_str = str(q)
        if "workflows" in q_str:
            m.scalar_one_or_none.return_value = mock_wf
        elif "execution_plans" in q_str:
            m.scalar_one_or_none.return_value = mock_plan
        else:
            m.scalars.return_value.all.return_value = [task_1a, task_1b, task_2]
        return m

    mock_session.execute.side_effect = mock_exec_side_effect

    svc = WorkflowRecoveryService()

    with (
        patch("app.workers.tasks.execution_tasks.execute_analysis_task.apply_async") as mock_apply,
        patch(
            "app.workers.tasks.execution_tasks.execute_agent_task.apply_async"
        ) as mock_agent_apply,
    ):
        result = await svc.resume_workflow(workflow_id=workflow_id, session=mock_session)

        # 1. Independent successful task is preserved
        assert result["preserved_tasks_count"] == 1
        assert "task_01_a" in result["preserved_tasks"]
        assert task_1a.output_data["findings"] == "Independent analysis ok"

        # 2. Dependent task is identified as blocked
        assert result["blocked_tasks_count"] == 1
        assert "task_02" in result["blocked_tasks"]

        # 3. No tasks were dispatched since dependent cannot run
        assert result["resumed_tasks_count"] == 0
        mock_apply.assert_not_called()
        mock_agent_apply.assert_not_called()

        # 4. Overall workflow reflects failed state due to permanently failed prerequisite
        assert mock_wf.status == WorkflowStatus.FAILED.value
