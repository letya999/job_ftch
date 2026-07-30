#!/usr/bin/env python3
"""Build a prefilter training JSONL from fetched candidates and manual labels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _read_labels(path: Path) -> dict[int, bool]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    labels: dict[int, bool] = {}
    for row in rows:
        idx = int(row["idx"])
        if idx in labels:
            raise SystemExit(f"duplicate label idx={idx}")
        labels[idx] = bool(row["gold"])
    return labels


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    candidates = _read_jsonl(args.candidates)
    labels = _read_labels(args.labels)
    candidate_ids = {int(row["idx"]) for row in candidates}
    missing = sorted(candidate_ids - set(labels))
    extra = sorted(set(labels) - candidate_ids)
    if missing or extra:
        raise SystemExit(f"label coverage mismatch: missing={missing[:20]} extra={extra[:20]}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    positives = 0
    with args.out.open("w", encoding="utf-8") as handle:
        for row in candidates:
            relevant = 1 if labels[int(row["idx"])] else 0
            positives += relevant
            handle.write(
                json.dumps(
                    {
                        "stable_id": row["stable_id"],
                        "text": row["text"],
                        "relevant": relevant,
                        "source_id": row.get("source_id"),
                        "url": row.get("url"),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    print(
        json.dumps(
            {"out": str(args.out), "rows": len(candidates), "positive": positives},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
