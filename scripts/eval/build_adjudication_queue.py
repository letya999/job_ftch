"""Build a deterministic human-adjudication queue without changing labels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from validate_eval_dataset import _ERROR_LABEL, content_hash, dataset_hash, load_rows


def _replay_disagreements(paths: list[Path]) -> dict[str, set[str]]:
    """Map parent IDs to replay mistakes without trusting replay labels as gold."""
    disagreements: dict[str, set[str]] = {}
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        results = payload.get("results", [])
        if not isinstance(results, list):
            raise ValueError(f"{path}: results must be a list")
        for result in results:
            if not isinstance(result, dict):
                continue
            stable_id = str(result.get("parent_stable_id") or result.get("stable_id") or "")
            accepted = result.get("pipeline_accepted")
            label = result.get("gold_relevant")
            if not stable_id or label not in (0, 1) or not isinstance(accepted, bool):
                continue
            if accepted and label == 0:
                disagreements.setdefault(stable_id, set()).add("model_false_positive")
            if not accepted and label == 1:
                disagreements.setdefault(stable_id, set()).add("model_false_negative")
    return disagreements


def queue_rows(
    rows: list[dict[str, Any]], *, replay_disagreements: dict[str, set[str]] | None = None
) -> list[dict[str, Any]]:
    replay_disagreements = replay_disagreements or {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(content_hash(row), []).append(row)
    queue: list[dict[str, Any]] = []
    for digest, members in sorted(grouped.items()):
        reasons: list[str] = []
        if len(members) > 1:
            reasons.append("duplicate_content")
        for row in members:
            if row.get("relevant") == 1:
                reasons.append("positive_requires_human_provenance")
            if row.get("relevant") == 1 and row.get("is_job") != 1:
                reasons.append("label_invariant_violation")
            if _ERROR_LABEL.search(str(row.get("reason", ""))):
                reasons.append("provider_error")
        disagreements = sorted(
            {
                reason
                for row in members
                for reason in replay_disagreements.get(str(row.get("stable_id", "")), set())
            }
        )
        if reasons or disagreements:
            exemplar = members[0]
            queue.append(
                {
                    "content_hash": digest,
                    "stable_ids": [str(row.get("stable_id", "")) for row in members],
                    "text": str(exemplar.get("text", "")),
                    "current_labels": [
                        {
                            "is_job": row.get("is_job"),
                            "relevant": row.get("relevant"),
                            "reason": row.get("reason"),
                        }
                        for row in members
                    ],
                    "reasons": sorted(set(reasons)),
                    "replay_disagreements": disagreements,
                    "status": "pending_human",
                }
            )
    return queue


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--replay",
        type=Path,
        action="append",
        default=[],
        help="Replay JSON artifact(s); prioritize their FP/FN parent observations.",
    )
    args = parser.parse_args()
    rows = load_rows(args.dataset)
    replay_disagreements = _replay_disagreements(args.replay)
    payload: dict[str, Any] = {
        "dataset_sha256": dataset_hash(args.dataset),
        "replay_artifacts": [str(path) for path in args.replay],
        "items": queue_rows(rows, replay_disagreements=replay_disagreements),
    }
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(payload['items'])} adjudication items to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
