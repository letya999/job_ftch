"""Build a narrow append-only repair patch for ``relevant=1 => is_job=1``."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from validate_eval_dataset import content_hash, dataset_hash, load_rows


def build_patch(dataset: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for row in load_rows(dataset):
        if row.get("relevant") != 1 or row.get("is_job") == 1:
            continue
        entries.append(
            {
                "stable_id": str(row["stable_id"]),
                "content_hash": content_hash(row),
                "old": {"is_job": row.get("is_job"), "relevant": row.get("relevant")},
                "new": {"is_job": 1, "relevant": 1},
                "reason": "The relevance-positive invariant requires vacancy jobness.",
                "status": "deterministic_repair",
                "repair_kind": "positive_requires_jobness",
                "repairer": "eval-dataset-validator",
                "rubric_version": "relevance-v2-responsibility-first",
            }
        )
    return {
        "original_dataset_hash": dataset_hash(dataset),
        "patch_version": "positive-requires-jobness-v1",
        "entries": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    patch = build_patch(args.dataset)
    args.output.write_text(json.dumps(patch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(patch['entries'])} positive-jobness repairs to {args.output}")


if __name__ == "__main__":
    main()
