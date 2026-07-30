"""Create cacheable, blind label-audit requests for unique dataset content.

This command deliberately does not send data to a provider.  The resulting
JSONL can be submitted to an approved cheap-model runner, then reconciled by
``build_adjudication_queue.py`` and ``apply_label_patch.py``.  Keeping request
generation offline makes audit scope, cost, and prompt/model snapshots explicit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from validate_eval_dataset import content_hash, dataset_hash, load_rows


def _cache_key(*, content: str, rubric: str, profile: str, model: str, prompt_version: str) -> str:
    return hashlib.sha256(
        "\x1f".join((content, rubric, profile, model, prompt_version)).encode("utf-8")
    ).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--rubric-version", required=True)
    parser.add_argument("--profile-hash", required=True)
    parser.add_argument("--prompt-version", default="label-audit-v1")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    rows = load_rows(args.dataset)
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        digest = content_hash(row)
        unique.setdefault(digest, row)
    requests = []
    for digest, row in sorted(unique.items()):
        text = str(row.get("text", ""))
        requests.append(
            {
                "content_hash": digest,
                "stable_ids": [
                    str(candidate.get("stable_id", ""))
                    for candidate in rows
                    if content_hash(candidate) == digest
                ],
                "cache_key": _cache_key(
                    content=digest,
                    rubric=args.rubric_version,
                    profile=args.profile_hash,
                    model=args.model,
                    prompt_version=args.prompt_version,
                ),
                "model": args.model,
                "rubric_version": args.rubric_version,
                "profile_hash": args.profile_hash,
                "prompt_version": args.prompt_version,
                "input": {
                    "text": text,
                    "response_schema": {
                        "is_job": "0|1|unknown",
                        "relevant": "0|1|unknown",
                        "reason": "string",
                        "evidence_spans": "string[]",
                    },
                },
            }
        )
    if args.limit > 0:
        requests = requests[: args.limit]
    args.output.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in requests)
        + ("\n" if requests else ""),
        encoding="utf-8",
    )
    print(json.dumps({"dataset_sha256": dataset_hash(args.dataset), "requests": len(requests)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
