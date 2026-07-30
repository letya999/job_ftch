"""Auto-label dirty JSONL items using heuristic PostTypeClassificationNode.

Reads all dirty JSONL files, classifies each item with the pipeline's rule-based
post-type heuristic (no LLM required), and writes golden labels to fixtures/dataset/.

Usage:
    python scripts/auto_label_dataset.py
    python scripts/auto_label_dataset.py --dirty .runtime/datasets/dirty/ --golden fixtures/dataset/labels.jsonl
    python scripts/auto_label_dataset.py --dirty .runtime/datasets/dirty/tg_vakansii_chatgpt_20260617.jsonl
    python scripts/auto_label_dataset.py --stats   # show label distribution only
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
from collections import Counter
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

load_dotenv(".env")
load_dotenv(".env.dev")


_ROUTING: dict[str, str] = {
    "job_posting": "ACCEPT",
    "candidate_seeking": "REVIEW",
    "announcement": "REJECT",
    "spam": "REJECT",
    "unknown": "REVIEW",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Auto-label dirty JSONL using heuristic classifier."
    )
    parser.add_argument(
        "--dirty",
        default=".runtime/datasets/dirty",
        help="Dirty JSONL file or directory.",
    )
    parser.add_argument(
        "--golden",
        default="fixtures/dataset/labels.jsonl",
        help="Output golden JSONL (default: fixtures/dataset/labels.jsonl).",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print label distribution for existing golden file and exit.",
    )
    return parser.parse_args()


def _load_dirty(dirty_path: Path) -> list[dict]:  # type: ignore
    paths = sorted(dirty_path.glob("*.jsonl")) if dirty_path.is_dir() else [dirty_path]
    items: list[dict] = []  # type: ignore
    for p in paths:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                with suppress(json.JSONDecodeError):
                    items.append(json.loads(line))
    return items


def _load_golden_ids(golden_path: Path) -> set[str]:
    if not golden_path.exists():
        return set()
    ids: set[str] = set()
    for line in golden_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            sid = rec.get("raw_item", {}).get("stable_id", "")
            if sid:
                ids.add(sid)
        except json.JSONDecodeError:
            pass
    return ids


def _print_stats(golden_path: Path) -> None:
    if not golden_path.exists():
        print(f"No golden file at {golden_path}")
        return
    post_types: Counter[str] = Counter()
    routing: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    total = 0
    for line in golden_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            total += 1
            post_types[rec.get("labels", {}).get("post_type", "?")] += 1
            routing[rec.get("labels", {}).get("routing_decision", "?")] += 1
            sources[rec.get("raw_item", {}).get("source_name", "?")] += 1
        except json.JSONDecodeError:
            pass

    print(f"\nGolden dataset: {golden_path}  ({total} items)\n")
    print("Post-type distribution:")
    for k, v in post_types.most_common():
        bar = "#" * (v * 30 // max(post_types.values()))
        print(f"  {k:<22} {v:>4}  {bar}")
    print("\nRouting distribution:")
    for k, v in routing.most_common():
        print(f"  {k:<10} {v:>4}")
    print("\nTop sources:")
    for k, v in sources.most_common(10):
        print(f"  {k:<35} {v:>4}")


async def _classify_item(raw_dict: dict[str, Any]) -> tuple[str, str]:
    """Return (post_type, routing_decision).

    Group sources (telegram_group) with 'unknown' post_type are REJECT:
    they're community discussions and are valuable negative examples.
    """
    from job_ftch.domain import RawItem, SourceKind
    from job_ftch.nodes.post_type import PostTypeClassificationNode

    try:
        item = RawItem.model_validate(raw_dict)
        node = PostTypeClassificationNode()
        result = await node.process(item)
        if result is None:
            return "unknown", "REVIEW"
        post_type: str = result.metadata.get("preclassified_post_type", "unknown")
        routing = _ROUTING.get(post_type, "REVIEW")

        # Group chats produce discussions/Q&A, not job postings.
        # Unknown items from groups are genuine negatives → REJECT.
        if post_type == "unknown" and item.source_kind == SourceKind.TELEGRAM_GROUP:
            routing = "REJECT"

        # Career sites always emit job postings — no editorial noise, no announcements.
        # Any item from a career site is a job posting regardless of heuristic label.
        if item.source_kind == SourceKind.CAREER_SITE and post_type != "job_posting":
            post_type = "job_posting"
            routing = "ACCEPT"

        return post_type, routing
    except Exception:
        return "unknown", "REVIEW"


async def main() -> int:
    args = parse_args()
    golden_path = Path(args.golden)

    if args.stats:
        _print_stats(golden_path)
        return 0

    dirty_path = Path(args.dirty)
    dirty_items = _load_dirty(dirty_path)
    if not dirty_items:
        print(f"No items found in {dirty_path}", file=sys.stderr)
        return 1

    existing_ids = _load_golden_ids(golden_path)
    pending = [it for it in dirty_items if it.get("stable_id", "") not in existing_ids]

    print(
        f"Dirty: {len(dirty_items)}  Already labeled: {len(existing_ids)}  Pending: {len(pending)}"
    )
    if not pending:
        print("Nothing to label.")
        return 0

    golden_path.parent.mkdir(parents=True, exist_ok=True)

    label_counts: Counter[str] = Counter()
    annotated = 0

    with golden_path.open("a", encoding="utf-8") as fh:
        for idx, raw_dict in enumerate(pending, 1):
            post_type, routing = await _classify_item(raw_dict)
            label_counts[post_type] += 1

            record = {
                "raw_item": raw_dict,
                "labels": {
                    "post_type": post_type,
                    "routing_decision": routing,
                    "note": "",
                },
                "expected": {},
                "annotated_at": datetime.now(UTC).isoformat(),
                "annotator": "auto_heuristic",
            }
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()
            annotated += 1

            if idx % 50 == 0 or idx == len(pending):
                print(f"  [{idx}/{len(pending)}] labeled")

    print(f"\nDone. Labeled {annotated} items -> {golden_path}")
    print("\nLabel distribution:")
    for label, count in label_counts.most_common():
        pct = count * 100 // annotated if annotated else 0
        print(f"  {label:<22} {count:>4}  ({pct}%)")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
