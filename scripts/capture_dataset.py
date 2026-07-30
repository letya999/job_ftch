"""Capture raw items from configured sources into dirty JSONL dataset files.

Usage:
    python scripts/capture_dataset.py
    python scripts/capture_dataset.py --config scripts/dataset_sources.yaml --out .runtime/datasets/dirty/
    python scripts/capture_dataset.py --sources tg_vakansii_chatgpt tg_ml_jobs_kz

Each source writes to its own JSONL file: .runtime/datasets/dirty/{source_name}_{YYYYMMDD}.jsonl
Deduplication by stable_id is applied per-source within a single run.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

from dotenv import load_dotenv

from job_ftch.application.registry import create_source_from_spec, load_extensions
from job_ftch.application.source_loader import load_sources
from job_ftch.domain import QuarantinedRawItem, RawItem

if TYPE_CHECKING:
    from job_ftch.application.contracts import Source

load_dotenv(".env")
load_dotenv(".env.dev")


def _configure_utf8_stdio() -> None:
    if getattr(sys.stdout, "buffer", None) is not None:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if getattr(sys.stderr, "buffer", None) is not None:
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture raw items from sources into dirty JSONL.")
    parser.add_argument(
        "--config",
        default="scripts/dataset_sources.yaml",
        help="Source config YAML (default: scripts/dataset_sources.yaml).",
    )
    parser.add_argument(
        "--out",
        default=".runtime/datasets/dirty",
        help="Output directory for dirty JSONL files (default: .runtime/datasets/dirty/).",
    )
    parser.add_argument(
        "--sources",
        nargs="*",
        help="Limit to specific source_names. Omit to capture all sources.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Override per-source item limit.",
    )
    parser.add_argument(
        "--total-limit",
        type=int,
        default=None,
        help="Hard cap for new items across every selected source.",
    )
    return parser.parse_args()


async def capture_source(
    source: Source[RawItem],
    out_file: Path,
    *,
    seen_ids: set[str],
    max_new_items: int | None = None,
) -> tuple[int, int]:
    """Fetch and append new items from one source. Returns (fetched, new)."""
    fetched = 0
    new_items = 0

    mode = "a" if out_file.exists() else "w"
    with out_file.open(mode, encoding="utf-8") as fh:
        async for raw in source.fetch():
            if max_new_items is not None and new_items >= max_new_items:
                break
            if isinstance(raw, QuarantinedRawItem):
                continue
            item = cast("RawItem", raw)  # type: ignore
            fetched += 1
            if item.stable_id in seen_ids:
                continue
            seen_ids.add(item.stable_id)
            line = item.model_dump_json(exclude={"schema_version"})
            fh.write(line + "\n")
            new_items += 1

    return fetched, new_items


def load_existing_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
            sid = record.get("stable_id", "")
            if sid:
                ids.add(sid)
        except json.JSONDecodeError:
            pass
    return ids


async def main() -> int:
    args = parse_args()
    if args.total_limit is not None and args.total_limit < 1:
        print("ERROR: --total-limit must be positive.", file=sys.stderr)
        return 1
    load_extensions()

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"ERROR: config not found: {config_path}", file=sys.stderr)
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    specs = load_sources(config_path)
    if args.sources:
        filter_set = set(args.sources)
        specs = [s for s in specs if s.source_name in filter_set]
        if not specs:
            print(f"ERROR: no sources matched: {args.sources}", file=sys.stderr)
            return 1

    date_str = datetime.now(UTC).strftime("%Y%m%d")
    total_fetched = 0
    total_new = 0

    for spec in specs:
        remaining = None if args.total_limit is None else args.total_limit - total_new
        if remaining is not None and remaining <= 0:
            break
        if args.limit is not None:
            spec = spec.model_copy(update={"limit": args.limit})

        out_file = out_dir / f"{spec.source_name}_{date_str}.jsonl"
        seen_ids = load_existing_ids(out_file)

        print(f"  {spec.source_name} → {out_file.name} (existing ids: {len(seen_ids)})")

        try:
            source = cast("Source[RawItem]", create_source_from_spec(spec))
            fetched, new_items = await capture_source(
                source,
                out_file,
                seen_ids=seen_ids,
                max_new_items=remaining,
            )
            print(f"    fetched={fetched}  new={new_items}")
            total_fetched += fetched
            total_new += new_items
        except Exception as exc:
            print(f"    ERROR: {exc}", file=sys.stderr)
            continue

    print(f"\nDone. total_fetched={total_fetched}  total_new={total_new}")
    return 0


if __name__ == "__main__":
    _configure_utf8_stdio()
    raise SystemExit(asyncio.run(main()))
