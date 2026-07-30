"""Merge a captured RawItem JSONL file with ordered manual label parts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--labels", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--deduplicate-stable-ids",
        action="store_true",
        help="Keep the first manually labelled occurrence of each stable_id.",
    )
    parser.add_argument(
        "--deduplicate-content",
        action="store_true",
        help="Keep the first occurrence of each whitespace-normalized text payload.",
    )
    args = parser.parse_args()

    raw_rows = _read_jsonl(args.raw)
    label_rows = [row for path in args.labels for row in _read_jsonl(path)]
    if len(raw_rows) != len(label_rows):
        raise ValueError(f"row count mismatch: raw={len(raw_rows)}, labels={len(label_rows)}")

    merged: list[dict[str, Any]] = []
    for index, (raw, label) in enumerate(zip(raw_rows, label_rows, strict=True), 1):
        if raw.get("stable_id") != label.get("stable_id"):
            raise ValueError(f"row {index}: stable_id mismatch")
        is_job, relevant = label.get("is_job"), label.get("relevant")
        if is_job not in (0, 1) or relevant not in (0, 1) or (relevant == 1 and is_job != 1):
            raise ValueError(f"row {index}: invalid manual label")
        row = dict(raw)
        row.update(label)
        row["label_history"] = [
            {
                "status": "adjudicated",
                "adjudicator": str(label["labeler"]),
                "new": {"is_job": is_job, "relevant": relevant},
            }
        ]
        merged.append(row)

    if args.deduplicate_stable_ids:
        unique_rows: dict[str, dict[str, Any]] = {}
        for row in merged:
            unique_rows.setdefault(str(row["stable_id"]), row)
        merged = list(unique_rows.values())
    if args.deduplicate_content:
        unique_rows = {}
        for row in merged:
            text = " ".join(str(row.get("text", "")).split())
            unique_rows.setdefault(hashlib.sha256(text.encode("utf-8")).hexdigest(), row)
        merged = list(unique_rows.values())

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in merged), encoding="utf-8"
    )
    print(f"merged={len(merged)} out={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
