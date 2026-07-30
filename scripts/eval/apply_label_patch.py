"""Apply a reviewed label patch to a new dataset file; the source is never rewritten."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from validate_eval_dataset import _ERROR_LABEL, content_hash, dataset_hash, load_rows


def _is_valid_deterministic_repair(entry: dict[str, Any], row: dict[str, Any]) -> bool:
    provider_failure_repair = (
        entry.get("status") == "deterministic_repair"
        and entry.get("repair_kind") == "provider_failure_label_unknown"
        and entry.get("repairer") == "eval-dataset-validator"
        and entry.get("new") == {"is_job": "unknown", "relevant": "unknown"}
        and row.get("is_job") == 0
        and _ERROR_LABEL.search(str(row.get("reason", ""))) is not None
    )
    positive_jobness_repair = (
        entry.get("status") == "deterministic_repair"
        and entry.get("repair_kind") == "positive_requires_jobness"
        and entry.get("repairer") == "eval-dataset-validator"
        and entry.get("new") == {"is_job": 1, "relevant": 1}
        and row.get("is_job") != 1
        and row.get("relevant") == 1
    )
    return provider_failure_repair or positive_jobness_repair


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("patch", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = load_rows(args.dataset)
    patch: dict[str, Any] = json.loads(args.patch.read_text(encoding="utf-8"))
    expected_hash = str(patch.get("original_dataset_hash", ""))
    actual_hash = dataset_hash(args.dataset)
    if expected_hash != actual_hash:
        raise ValueError(f"patch dataset hash mismatch: {expected_hash} != {actual_hash}")
    by_id = {str(row.get("stable_id")): row for row in rows}
    for entry in patch.get("entries", []):
        stable_id = str(entry["stable_id"])
        row = by_id.get(stable_id)
        if row is None:
            raise ValueError(f"patch references missing stable_id: {stable_id}")
        if entry.get("content_hash") != content_hash(row):
            raise ValueError(f"patch content hash mismatch: {stable_id}")
        old = entry.get("old", {})
        if {"is_job": row.get("is_job"), "relevant": row.get("relevant")} != old:
            raise ValueError(f"patch old labels do not match dataset: {stable_id}")
        new = entry.get("new", {})
        if new.get("relevant") == 1 and new.get("is_job") != 1:
            raise ValueError(f"patch violates relevant=>is_job: {stable_id}")
        is_adjudicated = entry.get("status") == "adjudicated" and bool(entry.get("adjudicator"))
        if not is_adjudicated and not _is_valid_deterministic_repair(entry, row):
            raise ValueError(
                f"patch entry is not reviewed or a valid deterministic repair: {stable_id}"
            )
        row.update(
            {
                "is_job": new.get("is_job"),
                "relevant": new.get("relevant"),
                "label_history": [*(row.get("label_history") or []), entry],
            }
        )
    args.output.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8"
    )
    print(f"wrote append-only projection to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
