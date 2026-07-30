from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.config import Settings
from job_ftch.domain import (
    JobRecord,
    ManagedCandidateProfile,
    SearchProfile,
    SourceKind,
    TenantConfig,
    create_job_group,
)
from job_ftch.domain.candidate import CandidateIdentity, CandidateProfile
from job_ftch.domain.tenant import ScheduleSpec


def _runner(*, schedule_seconds: int | None = None) -> TenantRunner:
    settings = Settings(
        llm_backend="heuristic",
        store_backend="memory",
        job_group_store_backend="memory",
        search_backend="sqlite",
        embedding_enabled=False,
    )
    tenant = TenantConfig(
        tenant_id="bot_runtime",
        display_name="Bot Runtime",
        store_backend="memory",
        job_group_store_backend="memory",
        search_backend="sqlite",
        llm_backend="heuristic",
        schedule=(
            ScheduleSpec(interval_seconds=schedule_seconds)
            if schedule_seconds is not None
            else None
        ),
    )
    return TenantRunner.from_tenants([tenant], base_settings=settings)


def _multi_runner() -> TenantRunner:
    settings = Settings(
        llm_backend="heuristic",
        store_backend="memory",
        job_group_store_backend="memory",
        search_backend="sqlite",
        embedding_enabled=False,
    )
    tenants = [
        TenantConfig(
            tenant_id="alpha",
            display_name="Alpha",
            store_backend="memory",
            job_group_store_backend="memory",
            search_backend="sqlite",
            llm_backend="heuristic",
        ),
        TenantConfig(
            tenant_id="beta",
            display_name="Beta",
            store_backend="memory",
            job_group_store_backend="memory",
            search_backend="sqlite",
            llm_backend="heuristic",
        ),
    ]
    return TenantRunner.from_tenants(tenants, base_settings=settings)


def _profile(user_id: str, role: str) -> ManagedCandidateProfile:
    profile_id = f"user_{user_id}"
    return ManagedCandidateProfile(
        user_id=user_id,
        profile_id=profile_id,
        profile=CandidateProfile(
            identity=CandidateIdentity(candidate_id=user_id, display_name=user_id),
            search_profiles=(SearchProfile(profile_id=profile_id, target_roles=(role,)),),
        ),
    )


@pytest.mark.asyncio
async def test_bot_schedule_falls_back_to_tenant_config_and_can_be_disabled() -> None:
    runner = _runner(schedule_seconds=900)

    assert await runner.get_schedule_interval("bot_runtime") == 900

    await runner.set_schedule_interval("bot_runtime", 3600)
    assert await runner.get_schedule_interval("bot_runtime") == 3600

    await runner.set_schedule_interval("bot_runtime", None)
    assert await runner.get_schedule_interval("bot_runtime") is None


@pytest.mark.asyncio
async def test_bot_publish_channel_stores_owner_and_keeps_core_posting_disabled() -> None:
    runner = _runner()

    await runner.set_publish_channel("bot_runtime", "@jobs_out", user_id="123")

    runtime = runner.get_runtime("bot_runtime")
    assert await runner.get_publish_channel("bot_runtime") == "@jobs_out"
    assert await runner.get_publish_user_id("bot_runtime") == "123"
    assert await runtime.store.get_run_state("config:posting_backend") == "none"
    assert runtime.settings.posting_backend == "none"


@pytest.mark.asyncio
async def test_runtime_catalog_can_be_scoped_to_one_bot_user() -> None:
    runner = _runner()
    await runner.save_and_activate_candidate_profile("bot_runtime", _profile("1", "ML"))
    await runner.save_and_activate_candidate_profile("bot_runtime", _profile("2", "DevOps"))

    runtime = runner.get_runtime("bot_runtime")
    catalog, _ = await runner._build_runtime_catalog(runtime, user_id="1")

    assert catalog.catalog_name == "user:1"
    assert len(catalog.profiles) == 1
    assert catalog.profiles[0].target_roles == ("ML",)


@pytest.mark.asyncio
async def test_bot_user_can_select_tenant() -> None:
    runner = _multi_runner()

    assert await runner.get_selected_tenant_id("123") == "alpha"

    await runner.set_selected_tenant_id("123", "beta")

    assert await runner.get_selected_tenant_id("123") == "beta"


@pytest.mark.asyncio
async def test_bot_scheduler_status_reads_store_state() -> None:
    runner = _runner()
    runtime = runner.get_runtime("bot_runtime")
    await runtime.store.set_run_state("bot_scheduler:last_attempt_at", "2026-06-30T10:00:00+00:00")
    await runtime.store.set_run_state("bot_scheduler:last_success_at", "2026-06-30T10:05:00+00:00")
    await runtime.store.set_run_state("bot_scheduler:last_error", "publish failed")
    await runtime.store.set_run_state("bot_scheduler:last_publish_sent", "3")

    status = await runner.get_bot_scheduler_status("bot_runtime")

    assert status["last_attempt_at"] == "2026-06-30T10:00:00+00:00"
    assert status["last_success_at"] == "2026-06-30T10:05:00+00:00"
    assert status["last_error"] == "publish failed"
    assert status["last_publish_sent"] == "3"


@pytest.mark.asyncio
async def test_latest_jobs_uses_larger_pool_for_profile_aware_rerank() -> None:
    runner = _runner()
    runtime = runner.get_runtime("bot_runtime")

    def _job(idx: int):
        job = JobRecord(
            raw_item_id=f"raw-{idx}",
            source_kind=SourceKind.DEBUG,
            source_name="fixture",
            source_record_id=f"src-{idx}",
            title=f"Job {idx}",
            description="desc",
        )
        return create_job_group(job)

    recorded_limits: list[int] = []

    async def _count(since=None) -> int:
        return 2500

    async def _list_groups(limit: int = 100, since=None):
        recorded_limits.append(limit)
        return [_job(i) for i in range(limit)]

    runtime.job_group_store.count = _count  # type: ignore[method-assign]
    runtime.job_group_store.list_groups = _list_groups  # type: ignore[method-assign]

    jobs = await runner.latest_jobs("bot_runtime", user_id="123", limit=5)

    assert len(jobs) == 5
    assert recorded_limits == [1000]


@pytest.mark.asyncio
async def test_latest_jobs_since_filters_before_limit() -> None:
    runner = _runner()
    runtime = runner.get_runtime("bot_runtime")
    cutoff = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)

    def _job(idx: int, fetched_at):
        job = JobRecord(
            raw_item_id=f"raw-{idx}",
            source_kind=SourceKind.DEBUG,
            source_name="fixture",
            source_record_id=f"src-{idx}",
            title=f"Job {idx}",
            description="desc",
            fetched_at=fetched_at,
        )
        return create_job_group(job)

    new_groups = [_job(i, cutoff - timedelta(days=1)) for i in range(80)]
    new_groups = [_job(100 + i, cutoff + timedelta(minutes=i)) for i in range(20)]

    async def _count(since=None) -> int:
        assert since == cutoff
        return len(new_groups)

    async def _list_groups(limit: int = 100, since=None):
        assert since == cutoff
        return (new_groups + new_groups) if since is None else new_groups

    runtime.job_group_store.count = _count  # type: ignore[method-assign]
    runtime.job_group_store.list_groups = _list_groups  # type: ignore[method-assign]

    jobs = await runner.latest_jobs("bot_runtime", limit=30, since=cutoff)

    assert len(jobs) == 20
    assert all(job.fetched_at is not None and job.fetched_at >= cutoff for job in jobs)


@pytest.mark.asyncio
async def test_resolve_candidate_profiles_uses_only_primary_active_profile() -> None:
    runner = _runner()
    p1 = _profile("1", "ML")
    p1 = p1.model_copy(update={"profile_id": "user_1_p1"})
    p2 = _profile("1", "DevOps")
    p2 = p2.model_copy(update={"profile_id": "user_1_p2"})

    await runner.save_and_activate_candidate_profile("bot_runtime", p1)
    await runner.save_and_activate_candidate_profile("bot_runtime", p2)

    records = await runner._resolve_candidate_profiles("bot_runtime", user_id="1")

    assert [record.profile_id for record in records] == ["user_1_p2"]


@pytest.mark.asyncio
async def test_has_candidate_profile_data_accepts_saved_but_inactive_profile() -> None:
    runner = _runner()
    runtime = runner.get_runtime("bot_runtime")
    profile = _profile("1", "ML")
    search_profile = profile.profile.search_profiles[0].model_copy(
        update={"positive_example_texts": ("Senior ML Engineer",)}
    )
    profile = profile.model_copy(
        update={
            "profile": profile.profile.model_copy(update={"search_profiles": (search_profile,)})
        }
    )

    await runtime.store.save_candidate_profile(profile)

    assert await runner.has_candidate_profile_data("bot_runtime", "1") is True
