"""Sync golden JSONL dataset to a Langfuse Dataset for M2 eval pipeline.

Reads fixtures/dataset/labels.jsonl (or --golden path), uploads each annotated
record as a Langfuse Dataset item. Idempotent: items already present (matched by
stable_id in metadata) are skipped unless --overwrite is passed.

Required env vars:
    JOB_FTCH_LANGFUSE_PUBLIC_KEY
    JOB_FTCH_LANGFUSE_SECRET_KEY
    JOB_FTCH_LANGFUSE_HOST   (default: https://cloud.langfuse.com)

Usage:
    python scripts/sync_to_langfuse.py
    python scripts/sync_to_langfuse.py --golden fixtures/dataset/labels.jsonl \\
        --dataset-name job_ftch_m2 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(".env")
load_dotenv(".env.dev")


def _langfuse_env() -> tuple[str, str, str]:
    public_key = os.environ.get("JOB_FTCH_LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.environ.get("JOB_FTCH_LANGFUSE_SECRET_KEY", "")
    host = os.environ.get("JOB_FTCH_LANGFUSE_HOST", "https://cloud.langfuse.com")
    return public_key, secret_key, host


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync golden JSONL to Langfuse Dataset.")
    parser.add_argument(
        "--golden",
        default="fixtures/dataset/labels.jsonl",
        help="Golden JSONL file (default: fixtures/dataset/labels.jsonl).",
    )
    parser.add_argument(
        "--dataset-name",
        default="job_ftch_m2",
        help="Langfuse dataset name (default: job_ftch_m2).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be uploaded without calling Langfuse.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-upload items already present in the dataset.",
    )
    return parser.parse_args()


def _load_golden(path: Path) -> list[dict]:  # type: ignore
    records: list[dict] = []  # type: ignore
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"  WARN: skipping malformed line: {exc}", file=sys.stderr)
    return records


def _existing_ids(client: object, dataset_name: str) -> set[str]:
    try:
        # langfuse SDK: client.get_dataset(name) returns Dataset with .items list
        dataset = client.get_dataset(dataset_name)  # type: ignore
        return {
            item.metadata.get("stable_id", "")
            for item in dataset.items
            if isinstance(item.metadata, dict)
        }
    except Exception:
        return set()


def main() -> int:
    args = parse_args()
    golden_path = Path(args.golden)

    if not golden_path.exists():
        print(f"ERROR: golden file not found: {golden_path}", file=sys.stderr)
        return 1

    records = _load_golden(golden_path)
    if not records:
        print("No records found in golden file.")
        return 0

    print(f"Golden records: {len(records)}")

    if args.dry_run:
        print(f"[DRY RUN] Would upload {len(records)} items to dataset '{args.dataset_name}'")
        for i, rec in enumerate(records[:3], 1):
            sid = rec.get("raw_item", {}).get("stable_id", "?")[:12]
            pt = rec.get("labels", {}).get("post_type", "?")
            rd = rec.get("labels", {}).get("routing_decision", "?")
            print(f"  [{i}] stable_id={sid}  post_type={pt}  routing={rd}")
        if len(records) > 3:
            print(f"  ... and {len(records) - 3} more")
        return 0

    public_key, secret_key, host = _langfuse_env()

    if not public_key or not secret_key:
        print(
            "ERROR: JOB_FTCH_LANGFUSE_PUBLIC_KEY and JOB_FTCH_LANGFUSE_SECRET_KEY must be set.",
            file=sys.stderr,
        )
        return 1

    try:
        from langfuse import Langfuse
    except ImportError:
        print(
            "ERROR: langfuse not installed. Run: pip install langfuse",
            file=sys.stderr,
        )
        return 1

    client = Langfuse(public_key=public_key, secret_key=secret_key, host=host)

    existing_ids = set() if args.overwrite else _existing_ids(client, args.dataset_name)
    print(f"Existing items in '{args.dataset_name}': {len(existing_ids)}")

    uploaded = 0
    skipped = 0

    for rec in records:
        raw_item = rec.get("raw_item", {})
        stable_id = raw_item.get("stable_id", "")
        labels = rec.get("labels", {})
        expected = rec.get("expected", {})

        if stable_id in existing_ids:
            skipped += 1
            continue

        item_input = {
            "source_kind": raw_item.get("source_kind"),
            "source_name": raw_item.get("source_name"),
            "url": raw_item.get("url"),
            "text": raw_item.get("text", "")[:2000],
        }
        item_expected_output = {
            "post_type": labels.get("post_type"),
            "routing_decision": labels.get("routing_decision"),
            **expected,
        }
        item_metadata = {
            "stable_id": stable_id,
            "annotated_at": rec.get("annotated_at"),
            "annotator": rec.get("annotator", "human"),
            "note": labels.get("note", ""),
        }

        client.create_dataset_item(
            dataset_name=args.dataset_name,
            input=item_input,
            expected_output=item_expected_output,
            metadata=item_metadata,
        )
        uploaded += 1

    client.flush()
    print(f"Uploaded: {uploaded}  Skipped (already exist): {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
