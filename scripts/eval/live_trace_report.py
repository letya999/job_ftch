#!/usr/bin/env python3
"""Summarize a production-shaped live run from emitted/rejected artifacts."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any


def _unwrap(record: Any) -> Any:
    if isinstance(record, dict) and "payload" in record and "schema_version" in record:
        return record["payload"]
    return record


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = _unwrap(json.loads(line))
            if not isinstance(value, dict):
                raise SystemExit(f"{path}:{line_no}: expected JSON object")
            rows.append(value)
    return rows


def _load_json_items(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    value = _unwrap(json.loads(path.read_text(encoding="utf-8")))
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        value = value["items"]
    if not isinstance(value, list):
        raise SystemExit(f"{path}: expected JSON list or object with items")
    return [item for item in (_unwrap(row) for row in value) if isinstance(item, dict)]


def _duration_seconds(summary: dict[str, Any]) -> float | None:
    started = summary.get("started_at")
    finished = summary.get("finished_at")
    if not isinstance(started, str) or not isinstance(finished, str):
        return None
    try:
        start = datetime.fromisoformat(started.replace("Z", "+00:00"))
        end = datetime.fromisoformat(finished.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max((end - start).total_seconds(), 0.0)


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _quantiles(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "count": 0,
            "min": None,
            "p10": None,
            "p25": None,
            "p50": None,
            "p75": None,
            "p90": None,
            "max": None,
            "avg": None,
        }
    ordered = sorted(values)

    def pick(q: float) -> float:
        return ordered[round((len(ordered) - 1) * q)]

    return {
        "count": len(ordered),
        "min": ordered[0],
        "p10": pick(0.10),
        "p25": pick(0.25),
        "p50": pick(0.50),
        "p75": pick(0.75),
        "p90": pick(0.90),
        "max": ordered[-1],
        "avg": mean(ordered),
    }


def _trace_events(row: dict[str, Any]) -> dict[str, Any]:
    trace = row.get("trace")
    if isinstance(trace, dict) and isinstance(trace.get("node_events"), dict):
        return trace["node_events"]
    return {}


def build_report(
    *,
    run_report_path: Path,
    rejected_path: Path,
    jobs_path: Path,
    review_path: Path,
) -> dict[str, Any]:
    run_report = json.loads(run_report_path.read_text(encoding="utf-8"))
    summary = run_report.get("summary", {})
    if not isinstance(summary, dict):
        raise SystemExit(f"{run_report_path}: missing summary object")

    rejected = _load_jsonl(rejected_path)
    accepted = _load_json_items(jobs_path)
    review = _load_jsonl(review_path)

    reason_counts = Counter(str(row.get("reason") or "unknown") for row in rejected)
    stage_counts = Counter(str(row.get("stage") or "unknown") for row in rejected)
    first_loss_counts: Counter[str] = Counter()
    node_outcomes: Counter[str] = Counter()
    by_source: dict[str, Counter[str]] = defaultdict(Counter)
    prefilter_drop_scores: list[float] = []
    prefilter_thresholds: Counter[str] = Counter()
    missing_prefilter_scores = 0

    for row in rejected:
        source_id = f"{row.get('source_kind') or 'unknown'}:{row.get('source_name') or 'unknown'}"
        by_source[source_id][str(row.get("reason") or "unknown")] += 1
        events = _trace_events(row)
        first_loss = None
        for node_id, event in events.items():
            if not isinstance(event, dict):
                continue
            outcome = str(event.get("outcome") or "unknown")
            node_outcomes[f"{node_id}:{outcome}"] += 1
            if first_loss is None and outcome in {"drop", "error", "timeout"}:
                first_loss = str(event.get("node_id") or node_id)
        first_loss_counts[first_loss or str(row.get("stage") or "unknown")] += 1

        prefilter_event = events.get("tfidf_logreg_prefilter")
        if isinstance(prefilter_event, dict) and prefilter_event.get("outcome") == "drop":
            score = prefilter_event.get("relevance_prefilter_score")
            threshold = prefilter_event.get("relevance_prefilter_threshold")
            if isinstance(score, (int, float)):
                prefilter_drop_scores.append(float(score))
            else:
                missing_prefilter_scores += 1
            if isinstance(threshold, (int, float)):
                prefilter_thresholds[f"{float(threshold):.6f}"] += 1

    fetched = int(summary.get("fetched") or 0)
    sanitized = int(summary.get("sanitized") or 0)
    triaged = int(summary.get("triaged") or 0)
    emitted = int(summary.get("emitted") or 0)
    reviewed = int(summary.get("review") or 0)
    deferred = int(summary.get("deferred") or 0)
    terminal = emitted + reviewed + deferred + len(rejected)

    return {
        "schema_version": 1,
        "run_report": str(run_report_path),
        "artifacts": {
            "jobs": str(jobs_path),
            "review": str(review_path),
            "rejected": str(rejected_path),
        },
        "source_run_id": summary.get("source_run_id"),
        "graph_hash": summary.get("graph_hash"),
        "counts": {
            "fetched": fetched,
            "sanitized": sanitized,
            "triaged": triaged,
            "extracted": int(summary.get("extracted") or 0),
            "accepted_jobs": len(accepted),
            "review_jobs": len(review),
            "emitted": emitted,
            "review": reviewed,
            "deferred": deferred,
            "rejected_artifacts": len(rejected),
            "terminal_accounted": terminal,
        },
        "conversion": {
            "sanitized_per_fetched": _ratio(sanitized, fetched),
            "triaged_per_sanitized": _ratio(triaged, sanitized),
            "emitted_per_fetched": _ratio(emitted, fetched),
            "bot_eligible_per_fetched": _ratio(
                int((run_report.get("bot_filter") or {}).get("eligible") or 0), fetched
            ),
        },
        "cost_latency": {
            "wall_seconds": _duration_seconds(summary),
            "llm_cost_usd": summary.get("llm_cost_usd"),
            "llm_usage_requests": summary.get("llm_usage_requests"),
            "llm_relevance_calls": summary.get("llm_relevance_calls"),
            "llm_latency_ms": summary.get("llm_latency_ms"),
        },
        "drop_reason_counts": dict(sorted(reason_counts.items())),
        "drop_stage_counts": dict(sorted(stage_counts.items())),
        "first_loss_node_counts": dict(sorted(first_loss_counts.items())),
        "node_outcome_counts": dict(sorted(node_outcomes.items())),
        "drop_reason_by_source": {
            source: dict(counter)
            for source, counter in sorted(
                by_source.items(), key=lambda item: (-sum(item[1].values()), item[0])
            )
        },
        "prefilter_drop_scores": {
            **_quantiles(prefilter_drop_scores),
            "missing_scores": missing_prefilter_scores,
            "threshold_counts": dict(sorted(prefilter_thresholds.items())),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-report", type=Path, required=True)
    parser.add_argument("--rejected", type=Path, default=Path("artifacts/ai_jobs/rejected.jsonl"))
    parser.add_argument("--jobs", type=Path, default=Path("artifacts/ai_jobs/jobs.json"))
    parser.add_argument("--review", type=Path, default=Path("artifacts/ai_jobs/review.jsonl"))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    report = build_report(
        run_report_path=args.run_report,
        rejected_path=args.rejected,
        jobs_path=args.jobs,
        review_path=args.review,
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
