from __future__ import annotations

import json

from scripts.eval.compare_policy_runs import compare


def _write(path, decisions: list[tuple[str, int, str]]) -> None:
    path.write_text(
        json.dumps(
            {
                "llm": {"calls": 3, "cost_usd": 0.12},
                "results": [
                    {
                        "stable_id": stable_id,
                        "gold_relevant": label,
                        "routing_decision": decision,
                        "pipeline_delivered": decision == "accept",
                        "duration_ms": duration,
                    }
                    for duration, (stable_id, label, decision) in enumerate(decisions, 1)
                ],
            }
        ),
        encoding="utf-8",
    )


def test_compare_reads_pipeline_eval_results_and_derives_runtime_stats(tmp_path) -> None:
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    _write(baseline, [("a", 1, "accept"), ("b", 0, "reject")])
    _write(candidate, [("a", 1, "reject"), ("b", 0, "accept")])

    report = compare(baseline, candidate)

    assert report["common_observations"] == 2
    assert report["baseline"]["true_positive"] == 1
    assert report["candidate"]["false_positive"] == 1
    assert report["candidate"]["stats"]["p95_latency_ms"] == 2.0
    assert "TP->FN" in report["transitions"]
