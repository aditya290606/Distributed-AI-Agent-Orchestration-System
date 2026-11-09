"""Unit tests for fault tolerance, task leasing, worker failure detection, circuit breaker, and retry policy."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AgentNotFoundError,
    AgentValidationError,
    LLMAuthenticationError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from app.db.models.execution_plan import ExecutionPlanModel
from app.db.models.recovery import WorkerNodeModel
from app.db.models.task import Task, TaskStatus
from app.db.models.workflow import Workflow, WorkflowStatus
from app.services.llm.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
)
from app.services.recovery.cancellation import WorkflowCancellationService
from app.services.recovery.failure_detector import WorkerFailureDetector
from app.services.recovery.lease_manager import TaskLeaseManager
from app.services.recovery.retry_policy import ErrorCategory, RetryPolicy
from app.services.recovery.task_recovery_manager import TaskRecoveryManager
from app.services.recovery.workflow_recovery import WorkflowRecoveryService

# ==============================================================================
# TaskLeaseManager Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_lease_manager_acquire_success():
    """Verify worker successfully acquires lease on unowned task."""
    task_id = uuid.uuid4()
    worker_id = "celery@node-1"

    mock_session = AsyncMock(spec=AsyncSession)
    mock_res = MagicMock()
    mock_res.rowcount = 1
    mock_session.execute.return_value = mock_res

    manager = TaskLeaseManager(default_lease_duration_seconds=300)
    token = await manager.acquire_lease(task_id=task_id, worker_id=worker_id, session=mock_session)

    assert token is not None
    assert len(token) == 32
    mock_session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_lease_manager_acquire_denied_already_leased():
    """Verify lease acquisition is rejected when task is already owned by another active worker."""
    task_id = uuid.uuid4()
    worker_id = "celery@node-2"

    mock_session = AsyncMock(spec=AsyncSession)
    mock_res = MagicMock()
    mock_res.rowcount = 0  # 0 rows updated because condition wasn't met
    mock_session.execute.return_value = mock_res

    manager = TaskLeaseManager()
    token = await manager.acquire_lease(task_id=task_id, worker_id=worker_id, session=mock_session)

    assert token is None


@pytest.mark.asyncio
async def test_lease_manager_renew_success():
    """Verify active lease is renewed when valid token is provided."""
    task_id = uuid.uuid4()
    token = "test-token-1234"

    mock_session = AsyncMock(spec=AsyncSession)
    mock_res = MagicMock()
    mock_res.rowcount = 1
    mock_session.execute.return_value = mock_res

    manager = TaskLeaseManager()
    renewed = await manager.renew_lease(task_id=task_id, lease_token=token, session=mock_session)

    assert renewed is True


@pytest.mark.asyncio
async def test_lease_manager_renew_rejected_invalid_token():
    """Verify renewal is rejected when token is mismatched or lease has already expired."""
    task_id = uuid.uuid4()
    invalid_token = "invalid-token"

    mock_session = AsyncMock(spec=AsyncSession)
    mock_res = MagicMock()
    mock_res.rowcount = 0
    mock_session.execute.return_value = mock_res

    manager = TaskLeaseManager()
    renewed = await manager.renew_lease(
        task_id=task_id, lease_token=invalid_token, session=mock_session
    )

    assert renewed is False


@pytest.mark.asyncio
async def test_lease_manager_release_success():
    """Verify lease is released and results committed when valid token is presented."""
    task_id = uuid.uuid4()
    token = "valid-token-abc"

    mock_session = AsyncMock(spec=AsyncSession)
    mock_res = MagicMock()
    mock_res.rowcount = 1
    mock_session.execute.return_value = mock_res

    manager = TaskLeaseManager()
    released = await manager.release_lease(
        task_id=task_id,
        lease_token=token,
        target_status=TaskStatus.SUCCEEDED,
        session=mock_session,
        result_data={"summary": "done"},
    )

    assert released is True


@pytest.mark.asyncio
async def test_lease_manager_release_rejected_zombie_worker():
    """Verify release is rejected when committing worker has lost lease ownership."""
    task_id = uuid.uuid4()
    lost_token = "expired-token"

    mock_session = AsyncMock(spec=AsyncSession)
    mock_res = MagicMock()
    mock_res.rowcount = 0  # Lease was revoked by recovery manager in the meantime!
    mock_session.execute.return_value = mock_res

    manager = TaskLeaseManager()
    released = await manager.release_lease(
        task_id=task_id,
        lease_token=lost_token,
        target_status=TaskStatus.SUCCEEDED,
        session=mock_session,
    )

    assert released is False


# ==============================================================================
# WorkerFailureDetector Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_failure_detector_record_heartbeat():
    """Verify recording heartbeat creates or updates WorkerNodeModel."""
    mock_session = AsyncMock(spec=AsyncSession)
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_res

    detector = WorkerFailureDetector()
    node = await detector.record_heartbeat(
        worker_id="celery@worker-node-1",
        session=mock_session,
        hostname="worker-host-1",
        queues=["default", "research"],
        active_tasks=[{"task_id": "t-1"}],
    )

    assert node.worker_id == "celery@worker-node-1"
    assert node.status == "ONLINE"
    assert node.active_task_count == 1
    assert "research" in node.registered_queues
    mock_session.add.assert_called_once()


@pytest.mark.asyncio
async def test_failure_detector_detect_unhealthy():
    """Verify detector identifies workers missing heartbeats."""
    now = datetime.now(UTC)
    stale_worker = WorkerNodeModel(
        worker_id="celery@dead-worker",
        status="SUSPECTED",
        last_heartbeat=now - timedelta(seconds=120),
        registered_queues=["default"],
        active_task_count=1,
    )

    mock_session = AsyncMock(spec=AsyncSession)
    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = [stale_worker]
    mock_session.execute.return_value = mock_res

    detector = WorkerFailureDetector(failure_threshold_seconds=60, grace_period_seconds=30)
    unhealthy = await detector.detect_unhealthy_workers(session=mock_session)

    assert len(unhealthy) == 1
    assert unhealthy[0]["worker_id"] == "celery@dead-worker"
    assert unhealthy[0]["status"] == "SUSPECTED"


# ==============================================================================
# RetryPolicy Tests
# ==============================================================================


def test_retry_policy_error_classification():
    """Verify classification into TRANSIENT vs PERMANENT vs TIMEOUT."""
    policy = RetryPolicy()

    # Permanent failures
    assert policy.classify_error(AgentValidationError("Invalid input")) == ErrorCategory.PERMANENT
    assert policy.classify_error(AgentNotFoundError("Unknown agent")) == ErrorCategory.PERMANENT
    assert policy.classify_error(LLMAuthenticationError("Bad API key")) == ErrorCategory.PERMANENT

    # Timeouts
    assert policy.classify_error(TimeoutError("Socket timed out")) == ErrorCategory.TIMEOUT
    assert policy.classify_error(LLMTimeoutError("LLM timed out")) == ErrorCategory.TIMEOUT

    # Transient
    assert policy.classify_error(LLMRateLimitError("429 Rate limit")) == ErrorCategory.TRANSIENT
    assert policy.classify_error(ConnectionError("Connection refused")) == ErrorCategory.TRANSIENT


def test_retry_policy_exponential_backoff_and_evaluation():
    """Verify bounded exponential backoff and decision limit."""
    policy = RetryPolicy(
        default_max_retries=3,
        initial_delay_seconds=2.0,
        max_delay_seconds=30.0,
        enable_jitter=False,
    )

    # 1. First retry on transient error -> should retry
    dec1 = policy.evaluate(ConnectionError("Network drop"), current_retry_count=0)
    assert dec1.should_retry is True
    assert dec1.delay_seconds == 2.0  # 2 * 2^0
    assert dec1.error_category == ErrorCategory.TRANSIENT

    # 2. Second retry
    dec2 = policy.evaluate(ConnectionError("Network drop"), current_retry_count=1)
    assert dec2.should_retry is True
    assert dec2.delay_seconds == 4.0  # 2 * 2^1

    # 3. Permanent error -> never retry
    dec_perm = policy.evaluate(AgentValidationError("Malformed payload"), current_retry_count=0)
    assert dec_perm.should_retry is False
    assert dec_perm.error_category == ErrorCategory.PERMANENT

    # 4. Max retries reached -> should not retry
    dec_exhausted = policy.evaluate(ConnectionError("Network drop"), current_retry_count=3)
    assert dec_exhausted.should_retry is False


# ==============================================================================
# CircuitBreaker Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_circuit_breaker_transitions():
    """Verify CLOSED -> OPEN -> HALF_OPEN -> CLOSED state lifecycle."""
    breaker = CircuitBreaker(
        failure_threshold=3,
        recovery_timeout_seconds=0.1,  # Short timeout for testing
        half_open_success_threshold=2,
    )

    assert breaker.state == CircuitState.CLOSED

    # 1. Record 2 failures -> stays CLOSED
    await breaker.record_failure(ConnectionError("Err 1"))
    await breaker.record_failure(ConnectionError("Err 2"))
    assert breaker.state == CircuitState.CLOSED

    # 2. 3rd failure -> trips to OPEN
    await breaker.record_failure(ConnectionError("Err 3"))
    assert breaker.state == CircuitState.OPEN

    # 3. While OPEN, execution is rejected immediately
    with pytest.raises(CircuitBreakerOpenError):
        await breaker.execute(AsyncMock())

    # 4. Wait for recovery timeout -> transitions to HALF_OPEN
    await asyncio.sleep(0.15)
    assert breaker.state == CircuitState.HALF_OPEN

    # 5. First successful probe in HALF_OPEN -> stays HALF_OPEN
    await breaker.record_success()
    assert breaker.state == CircuitState.HALF_OPEN

    # 6. Second successful probe in HALF_OPEN -> resets to CLOSED
    await breaker.record_success()
    assert breaker.state == CircuitState.CLOSED


# ==============================================================================
# TaskRecoveryManager Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_task_recovery_skips_already_succeeded():
    """Verify recovery never touches or requeues tasks that already succeeded."""
    task_id = uuid.uuid4()
    mock_task = MagicMock(spec=Task)
    mock_task.id = task_id
    mock_task.status = TaskStatus.SUCCEEDED.value

    mock_session = AsyncMock(spec=AsyncSession)
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = mock_task
    mock_session.execute.return_value = mock_res

    manager = TaskRecoveryManager()
    outcome = await manager.recover_task(task_id=task_id, session=mock_session)

    assert outcome["decision"] == "SKIPPED_ALREADY_SUCCESSFUL"
    assert outcome["status"] == TaskStatus.SUCCEEDED.value


@pytest.mark.asyncio
async def test_task_recovery_requeues_eligible_task():
    """Verify stuck/expired task is requeued and recovery event recorded."""
    task_id = uuid.uuid4()
    mock_task = MagicMock(spec=Task)
    mock_task.id = task_id
    mock_task.workflow_id = uuid.uuid4()
    mock_task.plan_id = uuid.uuid4()
    mock_task.planned_task_id = "task_01"
    mock_task.task_type = "research"
    mock_task.status = TaskStatus.RUNNING.value
    mock_task.recovery_count = 0
    mock_task.retry_count = 0
    mock_task.worker_id = "dead-worker"
    mock_task.lease_token = "stale-token"

    mock_session = AsyncMock(spec=AsyncSession)
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = mock_task
    mock_session.execute.return_value = mock_res

    manager = TaskRecoveryManager()

    with patch("app.workers.tasks.execution_tasks.execute_research_task.apply_async") as mock_apply:
        mock_apply.return_value = MagicMock(id="new-celery-id-123")
        outcome = await manager.recover_task(
            task_id=task_id, session=mock_session, reason="LEASE_EXPIRED"
        )

        assert outcome["decision"] == "REQUEUE"
        assert outcome["recovery_count"] == 1
        assert outcome["celery_task_id"] == "new-celery-id-123"
        mock_session.add.assert_called_once()  # RecoveryEventModel persisted!


@pytest.mark.asyncio
async def test_task_recovery_marks_failed_when_max_attempts_exceeded():
    """Verify task is marked permanently FAILED when recovery budget is exhausted."""
    task_id = uuid.uuid4()
    mock_task = MagicMock(spec=Task)
    mock_task.id = task_id
    mock_task.workflow_id = uuid.uuid4()
    mock_task.planned_task_id = "task_01"
    mock_task.task_type = "coding"
    mock_task.status = TaskStatus.RUNNING.value
    mock_task.recovery_count = 3  # Exhausted!
    mock_task.retry_count = 3

    mock_session = AsyncMock(spec=AsyncSession)
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = mock_task
    mock_session.execute.return_value = mock_res

    manager = TaskRecoveryManager(max_recovery_attempts=3)
    outcome = await manager.recover_task(
        task_id=task_id, session=mock_session, reason="STUCK_RUNNING"
    )

    assert outcome["decision"] == "MARK_FAILED"
    assert outcome["new_status"] == TaskStatus.FAILED.value
    assert "Recovery attempts exhausted" in outcome["reason"]


# ==============================================================================
# WorkflowCancellationService Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_workflow_cancellation():
    """Verify cancelling workflow revokes active tasks and preserves completed ones."""
    workflow_id = uuid.uuid4()

    mock_wf = MagicMock(spec=Workflow)
    mock_wf.id = workflow_id
    mock_wf.status = WorkflowStatus.RUNNING.value

    task_completed = MagicMock(spec=Task)
    task_completed.status = TaskStatus.SUCCEEDED.value

    task_running = MagicMock(spec=Task)
    task_running.id = uuid.uuid4()
    task_running.workflow_id = workflow_id
    task_running.status = TaskStatus.RUNNING.value
    task_running.celery_task_id = "celery-running-1"
    task_running.retry_count = 0
    task_running.planned_task_id = "task_2"

    mock_session = AsyncMock(spec=AsyncSession)

    def mock_exec_side_effect(q):
        m = MagicMock()
        q_str = str(q)
        if "workflows" in q_str:
            m.scalar_one_or_none.return_value = mock_wf
        else:
            m.scalars.return_value.all.return_value = [task_completed, task_running]
        return m

    mock_session.execute.side_effect = mock_exec_side_effect

    service = WorkflowCancellationService()

    with patch("app.workers.celery_app.celery_app.control.revoke") as mock_revoke:
        res = await service.cancel_workflow(workflow_id=workflow_id, session=mock_session)

        assert res["status"] == WorkflowStatus.CANCELLED.value
        assert res["cancelled_tasks_count"] == 1
        assert res["preserved_tasks_count"] == 1
        mock_revoke.assert_called_once_with("celery-running-1", terminate=True)
        assert task_running.status == TaskStatus.CANCELLED.value
        assert task_completed.status == TaskStatus.SUCCEEDED.value


# ==============================================================================
# WorkflowRecoveryService Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_workflow_resumption_dag_execution():
    """Verify workflow recovery preserves finished stage 1 tasks and resumes stage 2."""
    workflow_id = uuid.uuid4()
    plan_id = uuid.uuid4()

    mock_wf = MagicMock(spec=Workflow)
    mock_wf.id = workflow_id
    mock_wf.status = WorkflowStatus.RUNNING.value
    mock_wf.input_data = {"plan_id": str(plan_id)}

    # Stage 1 task is already SUCCEEDED
    task_1 = MagicMock(spec=Task)
    task_1.id = uuid.uuid4()
    task_1.workflow_id = workflow_id
    task_1.plan_id = plan_id
    task_1.planned_task_id = "task_01"
    task_1.status = TaskStatus.SUCCEEDED.value

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
                    "name": "Implementation",
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
            m.scalars.return_value.all.return_value = [task_1]
        return m

    mock_session.execute.side_effect = mock_exec_side_effect

    service = WorkflowRecoveryService()

    with patch("app.workers.tasks.execution_tasks.execute_coding_task.apply_async") as mock_apply:
        mock_apply.return_value = MagicMock(id="resumed-celery-id")
        res = await service.resume_workflow(workflow_id=workflow_id, session=mock_session)

        assert res["preserved_tasks_count"] == 1
        assert "task_01" in res["preserved_tasks"]
        assert res["resumed_tasks_count"] == 1
        assert "task_02" in res["resumed_tasks"]
        mock_apply.assert_called_once()
