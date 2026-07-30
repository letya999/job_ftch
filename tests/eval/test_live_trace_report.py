from __future__ import annotations

import json
import subprocess
from pathlib import Path

SCRIPT = Path("scripts/eval/live_trace_report.py")
PYTHON = str(Path(".venv/Scripts/python.exe"))


def test_live_trace_report_summarizes_wrapped_artifacts(tmp_path: Path) -> None:
    run_report = tmp_path / "run.json"
    run_report.write_text(
        json.dumps(
            {
                "summary": {
                    "fetched": 10,
                    "sanitized": 9,
                    "triaged": 2,
                    "extracted": 2,
                    "emitted": 1,
                    "review": 1,
                    "deferred": 0,
                    "source_run_id": "run-1",
                    "graph_hash": "graph-1",
                    "started_at": "2026-07-29 10:00:00+00:00",
                    "finished_at": "2026-07-29 10:00:05+00:00",
                    "llm_cost_usd": 0.01,
                    "llm_usage_requests": 2,
                    "llm_relevance_calls": 1,
                    "llm_latency_ms": 1000,
                },
                "bot_filter": {"eligible": 1},
            }
        ),
        encoding="utf-8",
    )
    rejected = tmp_path / "rejected.jsonl"
    rejected.write_text(
        json.dumps(
            {
                "schema_version": "job_ftch.rejected.v1",
                "payload": {
                    "reason": "low_relevance_prefilter",
                    "stage": "tfidf_logreg_prefilter",
                    "source_kind": "debug",
                    "source_name": "one",
                    "trace": {
                        "node_events": {
                            "dedup": {"node_id": "dedup", "outcome": "pass"},
                            "tfidf_logreg_prefilter": {
                                "node_id": "tfidf_logreg_prefilter",
                                "outcome": "drop",
                                "relevance_prefilter_score": 0.12,
                                "relevance_prefilter_threshold": 0.35,
                            },
                        }
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    jobs = tmp_path / "jobs.json"
    jobs.write_text(
        json.dumps({"schema_version": "job_ftch.job.v1", "items": [{"stable_id": "accepted"}]}),
        encoding="utf-8",
    )
    review = tmp_path / "review.jsonl"
    review.write_text(
        json.dumps({"schema_version": "job_ftch.job.v1", "payload": {"stable_id": "review"}})
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "trace.json"

    result = subprocess.run(
        [
            PYTHON,
            str(SCRIPT),
            "--run-report",
            str(run_report),
            "--rejected",
            str(rejected),
            "--jobs",
            str(jobs),
            "--review",
            str(review),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["counts"]["accepted_jobs"] == 1
    assert report["counts"]["review_jobs"] == 1
    assert report["drop_reason_counts"] == {"low_relevance_prefilter": 1}
    assert report["first_loss_node_counts"] == {"tfidf_logreg_prefilter": 1}
    assert report["prefilter_drop_scores"]["count"] == 1
    assert report["prefilter_drop_scores"]["p50"] == 0.12
    assert report["conversion"]["emitted_per_fetched"] == 0.1
    assert report["cost_latency"]["wall_seconds"] == 5.0
