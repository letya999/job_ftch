from datetime import UTC, datetime

from job_ftch.application.resolver import DeferredResolverQueue
from job_ftch.domain import ResolutionTask
from job_ftch.infrastructure.stores.in_memory import InMemoryStore


async def test_deferred_queue_is_idempotent_and_versioned() -> None:
    store = InMemoryStore()
    queue = DeferredResolverQueue(store)
    task = ResolutionTask(
        observation_id="obs-1",
        candidate_id="candidate-1",
        required_claims=("freshness",),
        resolver_name="freshness_probe",
        policy_version="evidence-v1",
        not_before=datetime.now(UTC),
    )

    await queue.enqueue(task)
    await queue.enqueue(task)

    pending = await queue.list_pending()
    assert len(pending) == 1
    assert pending[0].task_id == task.task_id

    await queue.mark_complete(task.task_id)
    assert await queue.list_pending() == ()


async def test_deferred_retry_updates_one_task() -> None:
    store = InMemoryStore()
    queue = DeferredResolverQueue(store)
    task = ResolutionTask(
        observation_id="obs-2",
        candidate_id="candidate-2",
        required_claims=("freshness:active_vacancy",),
        resolver_name="source_probe",
        policy_version="v1",
    )
    await queue.enqueue(task)
    when = datetime(2026, 1, 1, tzinfo=UTC)
    await queue.mark_retryable(task.task_id, attempt=1, not_before=when)
    pending = await queue.list_pending()
    assert pending[0].attempt == 1
    assert pending[0].not_before == when
