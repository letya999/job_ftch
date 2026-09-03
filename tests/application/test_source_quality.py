from __future__ import annotations

from datetime import UTC, datetime

from job_ftch.application.pipeline import RunSummary, SourceRunStats
from job_ftch.application.source_quality import (
    canonical_source_key,
    classify_source_quality,
    is_pipeline_run,
)
from job_ftch.domain import SourceHealth


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
    assert health.quality_window_runs == 0
