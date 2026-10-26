import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from structlog.testing import capture_logs

from job_ftch.application.pipeline import RunSummary
from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.config import Settings
from job_ftch.domain import IngestTask, IngestTaskState
from job_ftch.infrastructure.stores.in_memory import InMemoryStore
from job_ftch.infrastructure.stores.sqlite import SQLiteStore


@pytest.mark.asyncio
@pytest.mark.parametrize("store_factory", [InMemoryStore, SQLiteStore])
async def test_ingest_queue_cooldown_lease_and_recovery(store_factory) -> None:
    store = store_factory() if store_factory is InMemoryStore else store_factory(":memory:")
    now = datetime.now(UTC)
    task = IngestTask(
        task_id="task-1",
        tenant_id="tenant-1",
        run_id="run-1",
        source_id="career_site:hirify",
        rate_scope="tenant-1:hirify.me",
        available_at=now,
    )
    await store.enqueue_ingest_task(task)
    await store.record_ingest_rate_limit(
        task.rate_scope,
        cooldown_until=now + timedelta(seconds=10),
        retry_after_seconds=10,
        status_code=429,
    )
    assert (
        await store.claim_due_ingest_tasks(
            task.tenant_id, "worker-a", limit=1, lease_seconds=30, now=now
        )
        == ()
    )

    claimed = await store.claim_due_ingest_tasks(
        task.tenant_id,
        "worker-a",
        limit=1,
        lease_seconds=30,
        now=now + timedelta(seconds=11),
    )
    assert claimed[0].state == IngestTaskState.LEASED
    await store.defer_ingest_task(
        claimed[0],
        "worker-a",
        available_at=now + timedelta(seconds=20),
        error="429",
    )
    assert (await store.list_active_ingest_tasks(task.tenant_id))[0].state == (
        IngestTaskState.WAITING_RATE_LIMIT
    )
    await store.close()


@pytest.mark.asyncio
async def test_delayed_ingest_attempt_uses_fresh_run_identity() -> None:
    store = InMemoryStore()
    settings = Settings.model_validate({"ingest_queue_enabled": True})
    runtime = SimpleNamespace(store=store, settings=settings)
    runner = TenantRunner({})
    task = IngestTask(
        task_id="task-1",
        tenant_id="tenant-1",
        run_id="parent-run",
        source_id="career_site:hirify",
        rate_scope="hirify.me",
    )
    seen: list[str] = []

    async def fake_run(_tenant_id, **kwargs):
        seen.append(kwargs["run_id"])
        return RunSummary(completion_state="waiting_rate_limit")

    runner.run_tenant = fake_run  # type: ignore[method-assign]
    await runner._process_ingest_task(runtime, task)
    await runner._process_ingest_task(runtime, task)

    assert len(set(seen)) == 2
    assert "parent-run" not in seen


@pytest.mark.asyncio
async def test_delayed_ingest_attempt_logs_parent_and_attempt_identity() -> None:
    store = InMemoryStore()
    runtime = SimpleNamespace(store=store, settings=Settings.model_validate({}))
    runner = TenantRunner({})
    task = IngestTask(
        task_id="task-1", tenant_id="t", run_id="parent", source_id="source", rate_scope="scope"
    )
    seen: list[str] = []

    async def fake_run(_tenant_id, **kwargs):
        seen.append(kwargs["run_id"])
        return RunSummary(completion_state="waiting_rate_limit")

    runner.run_tenant = fake_run  # type: ignore[method-assign]
    with capture_logs() as logs:
        await runner._process_ingest_task(runtime, task)

    entries = [row for row in logs if row["event"] == "ingest_delayed_task_started"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["parent_run_id"] == "parent"
    assert entry["source_run_id"] == seen[0]


@pytest.mark.asyncio
async def test_delayed_attempt_reschedules_same_parent_task() -> None:
    store = InMemoryStore()
    settings = Settings.model_validate({"ingest_queue_enabled": True})
    runtime = SimpleNamespace(store=store, settings=settings, tenant=SimpleNamespace(tenant_id="t"))
    runner = TenantRunner({})
    task_id = hashlib.sha256(b"t:parent:career_site:hirify").hexdigest()
    task = IngestTask(
        task_id=task_id,
        tenant_id="t",
        run_id="parent",
        source_id="career_site:hirify",
        rate_scope="hirify.me",
    )
    await store.enqueue_ingest_task(task)
    claimed = await store.claim_due_ingest_tasks(
        "t", "worker", limit=1, lease_seconds=30, now=datetime.now(UTC)
    )
    assert len(claimed) == 1
    summary = RunSummary(
        tenant_id="t",
        source_run_id="new-attempt",
        source_outcomes=[{"source_id": task.source_id, "status": "rate_limited"}],
    )

    await runner._schedule_ingest_continuations(
        runtime,
        summary,
        max_items=None,
        user_id=None,
        bypass_override=None,
        parser_override=None,
        personal_mode=False,
        trigger="manual",
        ingest_task_id=task_id,
        ingest_worker_id="worker",
    )

    active = await store.list_active_ingest_tasks("t")
    assert len(active) == 1
    assert active[0].task_id == task_id
    assert active[0].run_id == "parent"
    assert active[0].state == IngestTaskState.WAITING_RATE_LIMIT


@pytest.mark.asyncio
async def test_runner_schedules_rate_limited_source_with_policy_cap() -> None:
    store = InMemoryStore()
    settings = Settings.model_validate(
        {
            "ingest_queue_enabled": True,
            "ingest_queue_max_wait_seconds": 600,
            "ingest_queue_default_retry_seconds": 30,
        }
    )
    runtime = SimpleNamespace(
        store=store,
        settings=settings,
        tenant=SimpleNamespace(tenant_id="tenant-1"),
        search_backend=None,
        job_backend=None,
        job_group_store=None,
        vector_backend=None,
    )
    runner = TenantRunner({})
    summary = RunSummary(
        tenant_id="tenant-1",
        source_run_id="run-1",
        source_outcomes=[
            {
                "source_id": "career_site:hirify",
                "status": "rate_limited",
                "zero_reason": "rate_limited",
                "rate_limit_scope": "hirify.me",
                "rate_limit_retry_after_seconds": 10,
            }
        ],
    )
    await runner._schedule_ingest_continuations(
        runtime,
        summary,
        max_items=500,
        user_id="user-1",
        bypass_override=None,
        parser_override="hirify",
        personal_mode=False,
        trigger="api",
        ingest_task_id=None,
        ingest_worker_id=None,
    )
    tasks = await store.list_active_ingest_tasks("tenant-1")
    assert summary.completion_state == "waiting_rate_limit"
    assert len(tasks) == 1
    assert tasks[0].max_items == 500
    assert tasks[0].parser_override == "hirify"
    assert tasks[0].available_at >= datetime.now(UTC) + timedelta(seconds=9)
    await runner.close()


@pytest.mark.asyncio
async def test_search_clones_queue_one_parent_source() -> None:
    store = InMemoryStore()
    runtime = SimpleNamespace(
        store=store,
        settings=Settings.model_validate({"ingest_queue_enabled": True}),
        tenant=SimpleNamespace(tenant_id="tenant-1"),
    )
    runner = TenantRunner({})
    summary = RunSummary(
        tenant_id="tenant-1",
        source_run_id="run-1",
        source_outcomes=[
            {"source_id": f"career_site:hireseeker_kw{i}", "status": "rate_limited"} for i in (1, 2)
        ],
    )

    await runner._schedule_ingest_continuations(
        runtime,
        summary,
        max_items=None,
        user_id=None,
        bypass_override=None,
        parser_override=None,
        personal_mode=False,
        trigger="scheduled",
        ingest_task_id=None,
        ingest_worker_id=None,
    )

    tasks = await store.list_active_ingest_tasks("tenant-1")
    assert [task.source_id for task in tasks] == ["career_site:hireseeker"]


@pytest.mark.asyncio
async def test_runner_does_not_retry_wait_longer_than_policy() -> None:
    store = InMemoryStore()
    settings = Settings.model_validate(
        {
            "ingest_queue_enabled": True,
            "ingest_queue_max_wait_seconds": 600,
        }
    )
    runtime = SimpleNamespace(
        store=store,
        settings=settings,
        tenant=SimpleNamespace(tenant_id="tenant-1"),
        search_backend=None,
        job_backend=None,
        job_group_store=None,
        vector_backend=None,
    )
    runner = TenantRunner({})
    summary = RunSummary(
        tenant_id="tenant-1",
        source_run_id="run-1",
        source_outcomes=[
            {
                "source_id": "career_site:hirify",
                "status": "rate_limited",
                "zero_reason": "rate_limited",
                "rate_limit_retry_after_seconds": 601,
            }
        ],
    )
    await runner._schedule_ingest_continuations(
        runtime,
        summary,
        max_items=None,
        user_id=None,
        bypass_override=None,
        parser_override=None,
        personal_mode=False,
        trigger="manual",
        ingest_task_id=None,
        ingest_worker_id=None,
    )
    assert summary.completion_state == "needs_operator"
    assert await store.list_active_ingest_tasks("tenant-1") == ()
    await runner.close()


@pytest.mark.asyncio
async def test_durable_worker_claims_and_completes_task() -> None:
    store = InMemoryStore()
    settings = Settings.model_validate(
        {
            "ingest_queue_enabled": True,
            "ingest_queue_poll_seconds": 0.01,
            "ingest_queue_lease_seconds": 30,
            "ingest_queue_max_attempts": 2,
            "ingest_queue_error_retry_seconds": 1,
        }
    )
    runtime = SimpleNamespace(
        store=store,
        settings=settings,
        tenant=SimpleNamespace(tenant_id="tenant-1"),
        search_backend=None,
        job_backend=None,
        job_group_store=None,
        vector_backend=None,
    )
    runner = TenantRunner({"tenant-1": runtime})
    task = IngestTask(
        task_id="task-worker",
        tenant_id="tenant-1",
        run_id="run-worker",
        source_id="career_site:hirify",
        rate_scope="tenant-1:hirify.me",
    )
    await store.enqueue_ingest_task(task)
    called = asyncio.Event()

    async def fake_run(*args, **kwargs):
        del args, kwargs
        called.set()
        return RunSummary(completion_state="completed")

    runner.run_tenant = fake_run  # type: ignore[method-assign]
    await runner.start()
    await asyncio.wait_for(called.wait(), timeout=1)
    await asyncio.sleep(0)
    active = await store.list_active_ingest_tasks("tenant-1")
    assert active == ()
    await runner.close()
