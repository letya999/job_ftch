"""Validate production-envelope replay data and emit a Phase-0 baseline report.

The report intentionally distinguishes observed coverage from labels that still
need human adjudication. It never fabricates a URL, source status, or score.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from job_ftch.domain import RawItem

REQUIRED_LABELS = frozenset(
    {
        "valid_observation",
        "segment_spans",
        "jobness",
        "field_correctness",
        "freshness",
        "risk",
        "relevance_by_profile",
        "duplicate_identity",
        "merge_identity",
        "delivery_decision",
    }
)


def load_replay_rows(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    seen: set[str] = set()
    for row in rows:
        observation_id = row.get("observation_id")
        if not isinstance(observation_id, str) or not observation_id or observation_id in seen:
            raise ValueError("Every replay row needs a unique non-empty observation_id.")
        seen.add(observation_id)
        payload = row.get("raw_payload_utf8")
        expected_hash = row.get("raw_payload_sha256")
        if (
            not isinstance(payload, str)
            or hashlib.sha256(payload.encode()).hexdigest() != expected_hash
        ):
            raise ValueError(f"{observation_id}: raw payload hash does not match.")
        RawItem.model_validate(row.get("raw_item"))
        envelope = row.get("envelope")
        if not isinstance(envelope, dict) or not envelope.get("source_status"):
            raise ValueError(f"{observation_id}: source envelope/status is required.")
        labels = row.get("labels")
        if not isinstance(labels, dict) or REQUIRED_LABELS - labels.keys():
            raise ValueError(f"{observation_id}: missing required stage labels.")
    return rows


def build_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    kinds = Counter(row["raw_item"]["source_kind"] for row in rows)
    statuses = Counter(row["envelope"]["source_status"] for row in rows)
    pending = sum(
        1
        for row in rows
        for label in row["labels"].values()
        if isinstance(label, dict) and label.get("adjudication") == "pending_human"
    )
    return {
        "dataset_kind": "production_envelope_replay_baseline",
        "observations": len(rows),
        "source_coverage": dict(sorted(kinds.items())),
        "source_status": dict(sorted(statuses.items())),
        "observations_without_url": sum(1 for row in rows if not row["raw_item"].get("url")),
        "pending_human_labels": pending,
        "metrics_not_yet_measured": [
            "stage_recall",
            "stage_precision",
            "segmentation_f1",
            "field_accuracy",
            "calibration",
            "cost",
            "p95_latency",
            "cache_hit_rate",
            "budget_deferrals",
            "false_merges",
            "lane_overlap",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("fixtures/dataset/raw_replay.jsonl"))
    parser.add_argument("--out", type=Path, default=Path("results/baseline_replay.json"))
    args = parser.parse_args()
    report = build_report(load_replay_rows(args.dataset))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
