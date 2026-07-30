#!/usr/bin/env python3
"""Build a full-surface confusion and false-negative loss report."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise SystemExit(f"{path}:{line_no}: expected JSON object")
            rows.append(row)
    return rows


def _load_positive_ids(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("positive_eval_ids"), list):
        return {str(item) for item in payload["positive_eval_ids"]}
    raise SystemExit("labels must be a JSON object with positive_eval_ids")


def _confusion(rows: list[dict[str, Any]], positive_ids: set[str]) -> dict[str, int | float]:
    tp = fp = fn = tn = 0
    for row in rows:
        gold = str(row.get("eval_id")) in positive_ids
        predicted = row.get("system_prediction") == "positive"
        if predicted and gold:
            tp += 1
        elif predicted:
            fp += 1
        elif gold:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def _trace_node_events(row: dict[str, Any]) -> dict[str, Any]:
    trace = row.get("trace")
    if isinstance(trace, dict) and isinstance(trace.get("node_events"), dict):
        return trace["node_events"]
    for key in ("node_events", "graph_node_events"):
        value = row.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _first_trace_loss(row: dict[str, Any]) -> str | None:
    for node_id, event in _trace_node_events(row).items():
        if not isinstance(event, dict):
            continue
        outcome = str(event.get("outcome") or "").strip()
        if outcome in {"drop", "error", "timeout"}:
            return str(event.get("node_id") or node_id)
        terminal_status = str(event.get("terminal_status") or "").strip().upper()
        if terminal_status in {"REJECT", "DEFERRED"}:
            return str(event.get("node_id") or node_id)
    return None


def _loss_stage(row: dict[str, Any]) -> str:
    if row.get("system_prediction") == "positive":
        return "not_loss"
    trace_loss = _first_trace_loss(row)
    if trace_loss:
        return trace_loss
    for key in ("loss_stage", "drop_stage", "exit_stage", "stage", "dropped_at"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    if row.get("routing_decision") == "review":
        return "review_not_published"
    if row.get("routing_decision") == "reject":
        return "terminal_reject"
    if row.get("best_score") is not None or row.get("quality_score") is not None:
        return "post_extraction_unpublished"
    if row.get("system_bucket") == "not_persisted":
        return "upstream_or_untraced_drop"
    return "unknown"


def _lineage_flags(row: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    if not row.get("stable_ids"):
        flags.append("missing_observation_stable_ids")
    if not row.get("url") and not row.get("external_id"):
        flags.append("missing_external_locator")
    if row.get("system_prediction") == "positive" and not row.get("routing_decision"):
        flags.append("published_without_routing_decision")
    if row.get("system_prediction") == "negative" and row.get("routing_decision") == "accept":
        flags.append("accept_not_mapped_to_system_positive")
    if (
        row.get("system_prediction") == "positive"
        and row.get("system_bucket") != "persisted_job_url_match"
    ):
        flags.append("positive_not_url_mapped")
    return flags


def build_report(
    *,
    candidates_path: Path,
    labels_path: Path,
) -> dict[str, Any]:
    rows = _load_jsonl(candidates_path)
    positive_ids = _load_positive_ids(labels_path)
    by_id = {str(row.get("eval_id")): row for row in rows}
    missing = sorted(positive_ids - set(by_id))
    if missing:
        raise SystemExit(f"labels reference {len(missing)} missing eval_ids: {missing[:10]}")

    fn_rows = [
        row
        for row in rows
        if str(row.get("eval_id")) in positive_ids and row.get("system_prediction") != "positive"
    ]
    loss_counts = Counter(_loss_stage(row) for row in fn_rows)
    source_counts = Counter(str(row.get("source_kind") or "unknown") for row in fn_rows)
    lineage_counts: Counter[str] = Counter()
    for row in rows:
        lineage_counts.update(_lineage_flags(row))

    false_negatives = [
        {
            "eval_id": row.get("eval_id"),
            "loss_stage": _loss_stage(row),
            "source_kind": row.get("source_kind"),
            "source_name": row.get("source_name"),
            "system_bucket": row.get("system_bucket"),
            "drop_reason": row.get("drop_reason") or row.get("reason"),
            "routing_decision": row.get("routing_decision"),
            "url": row.get("url"),
            "title": row.get("job_title"),
        }
        for row in fn_rows
    ]

    return {
        "schema_version": 1,
        "candidates": str(candidates_path),
        "labels": str(labels_path),
        "count": len(rows),
        "positive_count": len(positive_ids),
        "metrics": _confusion(rows, positive_ids),
        "false_negative_loss_counts": dict(sorted(loss_counts.items())),
        "false_negative_source_kind_counts": dict(sorted(source_counts.items())),
        "lineage_flag_counts": dict(sorted(lineage_counts.items())),
        "false_negatives": false_negatives,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    report = build_report(
        candidates_path=args.candidates,
        labels_path=args.labels,
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
