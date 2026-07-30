"""Rewrite an existing review JSONL artifact to the compact review schema."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from job_ftch.sinks.review_artifact import compact_review_payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    return parser


def compact_file(path: Path) -> int:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    count = 0
    with path.open(encoding="utf-8") as source, temporary.open("w", encoding="utf-8") as target:
        for line in source:
            if not line.strip():
                continue
            record = json.loads(line)
            payload = record.get("payload", record) if isinstance(record, dict) else record
            compact = {
                "schema_version": "job_ftch.review.v1",
                "payload": compact_review_payload(payload),
            }
            target.write(json.dumps(compact, ensure_ascii=True, sort_keys=True))
            target.write("\n")
            count += 1
    temporary.replace(path)
    return count


def main() -> int:
    args = _build_parser().parse_args()
    count = compact_file(args.path)
    print(json.dumps({"path": str(args.path), "rows": count, "bytes": args.path.stat().st_size}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
