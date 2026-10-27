from __future__ import annotations

from datetime import UTC, datetime

import pytest

from job_ftch.application.pipeline import RunSummary, SourceRunStats
from job_ftch.application.source_quality import (
    canonical_source_key,
    classify_source_quality,
    is_pipeline_run,
    quality_payload,
)
from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.config import Settings
from job_ftch.domain import SourceHealth, TenantConfig


def _run(
    *,
    run_id: str,
    fetched: int,
    outcomes: list[dict[str, object]],
    emitted_by: dict[str, int] | None = None,
) -> RunSummary:
    summary = RunSummary(
        fetched=fetched,
        source_run_id=run_id,
        finished_at=datetime.now(UTC),
        source_outcomes=outcomes,
    )
    for source_id, emitted in (emitted_by or {}).items():
        summary.by_source_id[source_id] = SourceRunStats(fetched=10, emitted=emitted)
    return summary


def test_canonical_source_key_collapses_keyword_fanout() -> None:
    assert canonical_source_key("career_site:hirehi_ru_kw4") == "hirehi_ru"
    assert canonical_source_key("career_site:hirehi_ru_kw3") == "hirehi_ru"
    assert canonical_source_key("career_site:hh_ru", "hh_ru") == "hh_ru"


def test_probe_runs_are_excluded_from_the_quality_window() -> None:
    probe = _run(
        run_id="probe",
        fetched=0,
        outcomes=[{"source_id": "career_site:x", "status": "ok", "yielded": 0}],
    )
    full = _run(
        run_id="full",
        fetched=100,
        outcomes=[
            {"source_id": f"career_site:s{i}", "status": "parsed_ok", "yielded": 10}
            for i in range(8)
        ],
    )
    assert is_pipeline_run(probe) is False
    assert is_pipeline_run(full) is True


def test_classify_marks_reliable_rich_and_high_relevance() -> None:
    runs = []
    for index in range(12):
        runs.append(
            _run(
                run_id=f"r{index}",
                fetched=200,
                outcomes=[
                    {
                        "source_id": "career_site:hh_ru",
                        "source_name": "hh_ru",
                        "status": "parsed_ok",
                        "yielded": 30,
                    },
                    {
                        "source_id": "career_site:superjob_ru",
                        "source_name": "superjob_ru",
                        "status": "waf_challenge",
                        "yielded": 0,
                    },
                    {
                        "source_id": "career_site:ozon",
                        "source_name": "ozon",
                        "status": "parsed_ok",
                        "yielded": 50,
                    },
                ],
                emitted_by={"career_site:hh_ru": 4, "career_site:ozon": 0},
            )
        )
    stats = classify_source_quality(runs)
    hh = stats["hh_ru"]
    assert hh.reliable is True
    assert hh.rich is True
    assert hh.high_relevance is True
    sj = stats["superjob_ru"]
    assert sj.reliable is False
    assert sj.rich is False
    ozon = stats["ozon"]
    assert ozon.reliable is True
    assert ozon.rich is True
    assert ozon.high_relevance is False
    payload = quality_payload(stats.values(), important=["hh_ru"])
    assert payload["important"] == ["hh_ru"]
    assert payload["watch"] == ["hh_ru"]
    assert "ozon" in payload["rich"]
    assert not hasattr(hh, "important")


def test_keyword_clones_count_as_one_source() -> None:
    runs = [
        _run(
            run_id="r1",
            fetched=80,
            outcomes=[
                {
                    "source_id": "career_site:geekjob_ru_kw1",
                    "status": "parsed_ok",
                    "yielded": 4,
                },
                {
                    "source_id": "career_site:geekjob_ru_kw2",
                    "status": "deadline_exceeded",
                    "yielded": 0,
                },
            ],
            emitted_by={"career_site:geekjob_ru_kw1": 1},
        )
        for _ in range(4)
    ]
    stats = classify_source_quality(runs)
    row = stats["geekjob_ru"]
    assert row.attempted == 4
    assert row.yield_hits == 4
    assert row.relevant_hits == 4
    assert row.rich is True
    assert row.high_relevance is True


def test_legacy_source_health_json_loads_quality_defaults() -> None:
    payload = {
        "source_id": "career_site:hh_ru",
        "source_kind": "career_site",
        "source_name": "hh_ru",
        "last_run_at": "2026-09-03T08:00:00+00:00",
        "last_success_at": "2026-09-03T08:00:00+00:00",
        "failure_streak": 0,
        "success_count": 3,
        "last_fetched": 36,
        "last_emitted": 4,
        "last_failed": 0,
        "last_quarantined": 0,
        "baseline_emitted": 4.0,
        "drift_ratio": 1.0,
        "degraded": False,
        "status": "healthy",
    }
    health = SourceHealth.model_validate(payload)
    assert health.quality_reliable is False
    assert health.quality_important is False
    assert health.quality_window_runs == 0


def test_operator_important_is_not_classified() -> None:
    runs = [
        _run(
            run_id=f"r{index}",
            fetched=200,
            outcomes=[
                {
                    "source_id": "career_site:habr_career",
                    "status": "deadline_exceeded",
                    "yielded": 0,
                },
                {
                    "source_id": "career_site:hh_ru",
                    "status": "parsed_ok",
                    "yielded": 20,
                },
            ],
            emitted_by={"career_site:hh_ru": 4},
        )
        for index in range(12)
    ]
    stats = classify_source_quality(runs)
    habr = stats["habr_career"]
    assert habr.reliable is False
    assert habr.high_relevance is False
    payload = quality_payload(stats.values(), important=["habr_career"])
    assert payload["important"] == ["habr_career"]
    assert payload["watch"] == ["habr_career"]


def test_important_never_appears_from_classify_source_quality() -> None:
    runs = [
        _run(
            run_id=f"r{index}",
            fetched=500,
            outcomes=[
                {
                    "source_id": "career_site:hh_ru",
                    "status": "parsed_ok",
                    "yielded": 100,
                },
                {
                    "source_id": "career_site:other",
                    "status": "parsed_ok",
                    "yielded": 10,
                },
            ],
            emitted_by={"career_site:hh_ru": 80},
        )
        for index in range(20)
    ]
    stats = classify_source_quality(runs)
    hh = stats["hh_ru"]
    assert hh.reliable is True
    assert hh.rich is True
    assert hh.high_relevance is True
    assert not hasattr(hh, "important")
    assert not hasattr(hh, "quality_important")
    assert "important" not in hh.as_dict()
    assert "quality_important" not in hh.as_health_update()


@pytest.mark.asyncio
async def test_source_dropping_out_of_window_resets_computed_labels_while_pinned_important_remains() -> (
    None
):
    now = datetime.now(UTC).isoformat()
    tenant = TenantConfig(tenant_id="ai_jobs", display_name="AI Jobs", sources=[])
    runner = TenantRunner.from_tenants([tenant], base_settings=Settings.model_validate({}))
    runtime = runner.get_runtime("ai_jobs")

    initial_health = SourceHealth(
        source_id="career_site:hh_ru",
        source_kind="career_site",
        source_name="hh_ru",
        last_run_at=now,
        last_success_at=now,
        failure_streak=0,
        success_count=10,
        last_fetched=100,
        last_emitted=50,
        last_failed=0,
        last_quarantined=0,
        baseline_emitted=5.0,
        drift_ratio=0.0,
        degraded=False,
        status="healthy",
        quality_window_runs=20,
        quality_ok_rate=1.0,
        quality_yield_rate=1.0,
        quality_relevant_rate=1.0,
        quality_reliable=True,
        quality_rich=True,
        quality_high_relevance=True,
        quality_important=True,
    )
    await runtime.store.save_source_health("career_site:hh_ru", initial_health)
    await runner.set_source_important("ai_jobs", "career_site:hh_ru", important=True)

    # 20 real pipeline runs where hh_ru is completely absent
    empty_runs = [
        _run(
            run_id=f"r{i}",
            fetched=50,
            outcomes=[
                {"source_id": "career_site:other1", "status": "parsed_ok", "yielded": 10},
                {"source_id": "career_site:other2", "status": "parsed_ok", "yielded": 10},
            ],
        )
        for i in range(20)
    ]
    for r in empty_runs:
        await runtime.store.save_run_summary(r)

    latest_summary = empty_runs[-1]
    await runner._apply_source_quality_window(runtime, latest_summary)

    updated_health = await runtime.store.get_source_health("career_site:hh_ru")
    assert updated_health is not None
    assert updated_health.quality_reliable is False
    assert updated_health.quality_rich is False
    assert updated_health.quality_high_relevance is False
    assert updated_health.quality_ok_rate == 0.0
    assert updated_health.quality_yield_rate == 0.0
    assert updated_health.quality_relevant_rate == 0.0
    assert updated_health.quality_important is True
