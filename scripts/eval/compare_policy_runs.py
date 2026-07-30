"""Compare two policy runs on the same observation envelope.

The script is intentionally dependency-free so it can run in CI and in a
shadow deployment. Each JSON object must contain ``observation_id`` and a
decision field (``decision`` or ``routing_decision``). Optional labels are
read from ``relevant``/``is_relevant``. Runtime counters can be supplied in a
top-level ``stats`` object or are simply reported as unavailable.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any


def _read(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = (
        payload.get("results") or payload.get("items") or payload
        if isinstance(payload, dict)
        else payload
    )
    if not isinstance(rows, list):
        raise ValueError(f"{path} must contain a JSON list or an items list")
    records: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("observation_id") or row.get("stable_id") or row.get("external_id") or "")
        if key:
            records[key] = row
    stats = payload.get("stats", {}) if isinstance(payload, dict) else {}
    if isinstance(payload, dict):
        stats = {**_derived_stats(payload, records), **stats}
    return records, stats if isinstance(stats, dict) else {}


def _derived_stats(payload: dict[str, Any], records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    durations = sorted(
        float(row["duration_ms"])
        for row in records.values()
        if isinstance(row.get("duration_ms"), (int, float))
    )
    llm = payload.get("llm") or {}
    errors = sum(1 for row in records.values() if row.get("error"))
    decisions = [str(row.get("routing_decision") or "").casefold() for row in records.values()]
    return {
        "llm_cost_usd": llm.get("cost_usd"),
        "llm_calls": llm.get("calls"),
        "p50_latency_ms": _percentile(durations, 0.50),
        "p95_latency_ms": _percentile(durations, 0.95),
        "error_rate": errors / len(records) if records else 0.0,
        "timeout_rate": sum(
            "timeout" in str(row.get("error", "")).casefold() for row in records.values()
        )
        / len(records)
        if records
        else 0.0,
        "deferred_rate": sum(decision == "deferred" for decision in decisions) / len(records)
        if records
        else 0.0,
    }


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    index = min(len(values) - 1, max(0, int(round((len(values) - 1) * fraction))))
    return round(values[index], 3)


def _decision(row: dict[str, Any]) -> str:
    return str(row.get("decision") or row.get("routing_decision") or "unknown").casefold()


def _label(row: dict[str, Any]) -> bool | None:
    value = row.get("relevant", row.get("is_relevant", row.get("gold_relevant")))
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    return None


def _metrics(rows: dict[str, dict[str, Any]]) -> dict[str, float | int]:
    return _metrics_list(list(rows.values()))


def _metrics_list(rows: list[dict[str, Any]]) -> dict[str, float | int]:
    labelled = [(row, _label(row)) for row in rows if _label(row) is not None]
    accepted = [row for row, _ in labelled if _decision(row) == "accept"]
    true_positive = sum(1 for row, label in labelled if _decision(row) == "accept" and label)
    false_positive = sum(1 for row, label in labelled if _decision(row) == "accept" and not label)
    false_negative = sum(1 for row, label in labelled if _decision(row) != "accept" and label)
    precision = true_positive / len(accepted) if accepted else 0.0
    recall = (
        true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    )
    return {
        "labelled": len(labelled),
        "accepted": len(accepted),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(2 * precision * recall / (precision + recall), 6)
        if precision + recall
        else 0.0,
    }


def _percentile(values: list[float], fraction: float) -> float:
    values = sorted(values)
    index = min(len(values) - 1, max(0, int(round((len(values) - 1) * fraction))))
    return round(values[index], 6)


def _bootstrap_intervals(
    baseline_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
    *,
    seed: int = 42,
    iterations: int = 1000,
) -> dict[str, dict[str, float]]:
    if not baseline_rows:
        return {}
    rng = random.Random(seed)
    baseline_values: dict[str, list[float]] = {name: [] for name in ("precision", "recall", "f1")}
    candidate_values: dict[str, list[float]] = {name: [] for name in ("precision", "recall", "f1")}
    size = len(baseline_rows)
    for _ in range(iterations):
        indices = [rng.randrange(size) for _ in range(size)]
        base_sample = [baseline_rows[index] for index in indices]
        candidate_sample = [candidate_rows[index] for index in indices]
        base_metrics = _metrics_list(base_sample)
        candidate_metrics = _metrics_list(candidate_sample)
        for name in baseline_values:
            baseline_values[name].append(float(base_metrics[name]))
            candidate_values[name].append(float(candidate_metrics[name]))
    return {
        name: {
            "baseline_low": _percentile(baseline_values[name], 0.025),
            "baseline_high": _percentile(baseline_values[name], 0.975),
            "candidate_low": _percentile(candidate_values[name], 0.025),
            "candidate_high": _percentile(candidate_values[name], 0.975),
            "delta_low": _percentile(
                [candidate_values[name][i] - baseline_values[name][i] for i in range(iterations)],
                0.025,
            ),
            "delta_high": _percentile(
                [candidate_values[name][i] - baseline_values[name][i] for i in range(iterations)],
                0.975,
            ),
        }
        for name in baseline_values
    }


def compare(baseline: Path, candidate: Path) -> dict[str, Any]:
    base_rows, base_stats = _read(baseline)
    candidate_rows, candidate_stats = _read(candidate)
    common = set(base_rows) & set(candidate_rows)
    ordered = sorted(common)
    transitions: dict[str, list[str]] = {}
    regressions: list[str] = []
    for key in ordered:
        before, after = _classification(base_rows[key]), _classification(candidate_rows[key])
        transition = f"{before}->{after}"
        transitions.setdefault(transition, []).append(key)
        if transition in {"TP->FN", "TN->FP"}:
            regressions.append(key)
    baseline_metrics = _metrics({key: base_rows[key] for key in common})
    candidate_metrics = _metrics({key: candidate_rows[key] for key in common})
    paired_baseline_rows = [base_rows[key] for key in ordered]
    paired_candidate_rows = [candidate_rows[key] for key in ordered]
    return {
        "common_observations": len(common),
        "decision_changes": sum(
            _decision(base_rows[key]) != _decision(candidate_rows[key]) for key in common
        ),
        "baseline": {**baseline_metrics, "stats": base_stats},
        "candidate": {**candidate_metrics, "stats": candidate_stats},
        "deltas": {
            key: round(float(candidate_metrics[key]) - float(baseline_metrics[key]), 6)
            for key in ("precision", "recall", "f1")
        },
        "confidence_intervals": _bootstrap_intervals(paired_baseline_rows, paired_candidate_rows),
        "transitions": {key: value for key, value in sorted(transitions.items())},
        "regression_items": regressions,
        "promotion": _promotion(
            baseline_metrics, candidate_metrics, base_stats, candidate_stats, regressions
        ),
    }


def _promotion(
    baseline: dict[str, float | int],
    candidate: dict[str, float | int],
    base_stats: dict[str, Any],
    candidate_stats: dict[str, Any],
    regressions: list[str],
) -> dict[str, Any]:
    """Conservative machine-readable gates; missing controlled metrics are inconclusive."""
    checks = {
        "recall_strictly_improves": float(candidate["recall"]) > float(baseline["recall"]),
        "precision_non_regression": float(candidate["precision"]) >= float(baseline["precision"]),
        "no_unexplained_regression_items": not any(item for item in regressions),
    }
    for metric in (
        "llm_cost_usd",
        "p50_latency_ms",
        "p95_latency_ms",
        "error_rate",
        "timeout_rate",
        "deferred_rate",
    ):
        base_value, candidate_value = base_stats.get(metric), candidate_stats.get(metric)
        checks[f"{metric}_non_regression"] = (
            candidate_value <= base_value
            if isinstance(base_value, (int, float)) and isinstance(candidate_value, (int, float))
            else None
        )
    status = "pass" if all(value is True for value in checks.values()) else "inconclusive"
    return {"status": status, "checks": checks}


def _classification(row: dict[str, Any]) -> str:
    label = _label(row)
    accepted = _decision(row) == "accept"
    if label is None:
        return "UNLABELLED"
    if accepted and label:
        return "TP"
    if accepted:
        return "FP"
    if label:
        return "FN"
    return "TN"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            compare(args.baseline, args.candidate), ensure_ascii=False, indent=2, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
