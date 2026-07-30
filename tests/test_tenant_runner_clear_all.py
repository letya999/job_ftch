import pytest

from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.config import Settings
from job_ftch.domain import CareerSiteSpec, JobRecord, SourceKind, TenantConfig


@pytest.mark.anyio
async def test_tenant_runner_clear_all():
    settings = Settings(store_backend="memory", job_group_store_backend="memory")
    tenant = TenantConfig(
        tenant_id="test",
        display_name="Test",
        sources=(CareerSiteSpec(url="http://example.com"),),
        store_backend="memory",
        job_group_store_backend="memory",
    )
    runner = TenantRunner.from_tenants([tenant], base_settings=settings)

    runtime = runner.get_runtime("test")
    job = JobRecord(
        stable_id="job1",
        raw_item_id="raw1",
        title="Software Engineer",
        company="Google",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="tg",
        description="desc",
        canonical_url="http://google.com/job1",
    )
    await runtime.job_group_store.create(job)
    await runtime.store.mark_processed("raw1")

    initial_count = await runtime.job_group_store.count()
    dedup, jobs, groups, vectors = await runner.clear_all("test")
    assert jobs == 0
    assert groups == initial_count
    assert vectors == 0
    assert await runtime.job_group_store.count() == 0
    assert not await runtime.store.has_processed("raw1")


@pytest.mark.anyio
async def test_latest_jobs_min_score():
    settings = Settings(store_backend="memory", job_group_store_backend="memory")
    tenant = TenantConfig(
        tenant_id="test",
        display_name="Test",
        sources=(CareerSiteSpec(url="http://example.com"),),
        store_backend="memory",
        job_group_store_backend="memory",
    )
    runner = TenantRunner.from_tenants([tenant], base_settings=settings)

    runtime = runner.get_runtime("test")
    job1 = JobRecord(
        stable_id="job1",
        raw_item_id="raw1",
        title="Good Job",
        company="Google",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="tg",
        description="desc",
        best_score=0.9,
    )
    job2 = JobRecord(
        stable_id="job2",
        raw_item_id="raw2",
        title="Bad Job",
        company="Google",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="tg",
        description="desc",
        best_score=0.1,
    )

    await runtime.job_group_store.create(job1)
    await runtime.job_group_store.create(job2)

    # Check that best_score is loaded correctly
    groups = await runtime.job_group_store.list_groups()
    for g in groups:
        if "Good Job" in g.canonical_job.title:
            assert g.canonical_job.best_score == 0.9
        if "Bad Job" in g.canonical_job.title:
            assert g.canonical_job.best_score == 0.1

    jobs = await runner.latest_jobs("test", min_score=0.5)
    assert len(jobs) == 1
    assert jobs[0].title == "Good Job"
