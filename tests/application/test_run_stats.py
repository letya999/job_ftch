from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from job_ftch.application.pipeline import RunSummary, SourceRunStats
from job_ftch.application.run_stats import build_pipeline_run_stats, build_source_run_stats
from job_ftch.application.source_quality import SourceQualityStats
from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.config import Settings
from job_ftch.domain import TenantConfig


def test_build_pipeline_and_source_run_stats() -> None:
    started = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    summary = RunSummary(
        fetched=100,
        extracted=20,
        emitted=5,
        review=10,
        rejected=80,
        dropped=5,
        source_run_id="run-1",
        tenant_id="ai_jobs",
        started_at=started,
        finished_at=started + timedelta(minutes=8),
        source_outcomes=[
            {
                "source_id": "career_site:hh_ru",
                "source_kind": "career_site",
                "source_name": "hh_ru",
                "status": "parsed_ok",
                "yielded": 40,
            },
            {
                "source_id": "career_site:superjob_ru",
                "source_kind": "career_site",
                "source_name": "superjob_ru",
                "status": "waf_challenge",
                "yielded": 0,
                "error": "waf",
            },
        ],
    )
    summary.by_source_id["career_site:hh_ru"] = SourceRunStats(
        fetched=40, extracted=12, emitted=5, llm_cost_usd=0.12, llm_latency_ms=900
    )
    summary.llm_cost_usd = 0.2
    summary.llm_latency_ms = 1200
    pipeline = build_pipeline_run_stats(summary)
    assert pipeline.source_count == 2
    assert pipeline.ok_sources == 1
    assert pipeline.fail_sources == 1
    assert pipeline.duration_ms == 8 * 60 * 1000
    assert pipeline.conversion_extract == 0.2
    assert pipeline.conversion_accept == 0.05
    quality = {
        "hh_ru": SourceQualityStats(
            source_key="hh_ru",
            window_runs=20,
            attempted=20,
            ok=20,
            fail=0,
            yield_hits=20,
            relevant_hits=18,
            yield_sum=800,
            emitted_sum=80,
            ok_rate=1.0,
            yield_rate=1.0,
            relevant_rate=0.9,
            reliable=True,
            rich=True,
            high_relevance=True,
        )
    }
    rows = build_source_run_stats(summary, quality=quality, important={"hh_ru": True})
    by_key = {item.source_key: item for item in rows}
    assert by_key["hh_ru"].quality_important is True
    assert by_key["hh_ru"].quality_reliable is True
    assert by_key["hh_ru"].emitted == 5
    assert by_key["superjob_ru"].quality_important is False
    assert by_key["superjob_ru"].status == "waf_challenge"


@pytest.mark.asyncio
async def test_tenant_runner_run_stats_lifecycle() -> None:
    now = datetime.now(UTC)
    tenant = TenantConfig(tenant_id="ai_jobs", display_name="AI Jobs", sources=[])
    runner = TenantRunner.from_tenants([tenant], base_settings=Settings.model_validate({}))
    runtime = runner.get_runtime("ai_jobs")
    store = runtime.store._store

    # 1. lock-skip: skipped_already_active is True -> stats NOT written
    lock_skip_summary = RunSummary(
        source_run_id="run-skip",
        skipped_already_active=True,
        started_at=now,
        finished_at=now,
    )
    await runner._apply_source_quality_window(runtime, lock_skip_summary)
    assert len(getattr(store, "_pipeline_run_stats", {})) == 0
    assert len(getattr(store, "_source_run_stats", {})) == 0

    # 2. no source_run_id -> stats NOT written
    no_id_summary = RunSummary(
        source_run_id=None,
        started_at=now,
        finished_at=now,
    )
    await runner._apply_source_quality_window(runtime, no_id_summary)
    assert len(getattr(store, "_pipeline_run_stats", {})) == 0
    assert len(getattr(store, "_source_run_stats", {})) == 0

    # 3. real run: writes both pipeline_run_stats and source_run_stats
    real_summary = RunSummary(
        tenant_id="ai_jobs",
        source_run_id="run-real-1",
        started_at=now,
        finished_at=now + timedelta(minutes=5),
        fetched=20,
        extracted=10,
        emitted=4,
        source_outcomes=[
            {
                "source_id": "career_site:hh_ru",
                "source_kind": "career_site",
                "source_name": "hh_ru",
                "status": "parsed_ok",
                "yielded": 10,
            },
            {
                "source_id": "career_site:superjob_ru",
                "source_kind": "career_site",
                "source_name": "superjob_ru",
                "status": "parsed_ok",
                "yielded": 10,
            },
        ],
    )
    real_summary.by_source_id["career_site:hh_ru"] = SourceRunStats(fetched=10, emitted=4)
    real_summary.by_source_id["career_site:superjob_ru"] = SourceRunStats(fetched=10, emitted=0)

    await runner._apply_source_quality_window(runtime, real_summary)
    pipeline_stats = getattr(store, "_pipeline_run_stats", {})
    source_stats = getattr(store, "_source_run_stats", {})
    assert ("ai_jobs", "run-real-1") in pipeline_stats
    assert ("ai_jobs", "run-real-1", "career_site:hh_ru") in source_stats
    pipeline_row = pipeline_stats[("ai_jobs", "run-real-1")]
    assert pipeline_row.source_run_id == "run-real-1"
    assert pipeline_row.fetched == 20

    # 4. dual-write failure: save_pipeline_run_stats raises, but run does NOT throw
    failing_summary = RunSummary(
        tenant_id="ai_jobs",
        source_run_id="run-fail-dualwrite",
        started_at=now,
        finished_at=now + timedelta(minutes=1),
        fetched=20,
        source_outcomes=[
            {"source_id": "career_site:hh_ru", "status": "parsed_ok", "yielded": 10},
        ],
    )
    original_save = runtime.store.save_pipeline_run_stats

    async def mock_save_pipeline(*args: object, **kwargs: object) -> None:
        raise RuntimeError("database down")

    runtime.store.save_pipeline_run_stats = mock_save_pipeline
    # Must complete cleanly without raising
    await runner._apply_source_quality_window(runtime, failing_summary)
    runtime.store.save_pipeline_run_stats = original_save
