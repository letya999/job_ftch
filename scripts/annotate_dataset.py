"""Interactive CLI annotation tool for the M2 golden dataset.

Reads dirty JSONL files, pre-fills labels with the pipeline's heuristic predictions,
and prompts the human annotator to confirm or override each item.

Usage:
    python scripts/annotate_dataset.py
    python scripts/annotate_dataset.py --dirty .runtime/datasets/dirty/ --golden fixtures/dataset/labels.jsonl
    python scripts/annotate_dataset.py --dirty .runtime/datasets/dirty/tg_vakansii_chatgpt_20260617.jsonl

Label schema saved per item:
  {
    "raw_item": { ...RawItem fields... },
    "labels": {
      "post_type": "job_posting" | "not_a_job" | "announcement" | ...,
      "routing_decision": "ACCEPT" | "REVIEW" | "REJECT",
      "note": ""          # optional free-text annotator note
    },
    "expected": {
      "title": "...",     # pipeline-predicted extraction, confirmed/overridden by human
      "company": "...",
      "seniority": "...",
      "work_mode": "..."
    },
    "annotated_at": "2026-06-17T...",
    "annotator": "human"
  }
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import textwrap
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(".env")
load_dotenv(".env.dev")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Interactively annotate dirty JSONL into golden labels."
    )
    parser.add_argument(
        "--dirty",
        default=".runtime/datasets/dirty",
        help="Dirty JSONL file or directory (default: .runtime/datasets/dirty/).",
    )
    parser.add_argument(
        "--golden",
        default="fixtures/dataset/labels.jsonl",
        help="Output golden JSONL file (default: fixtures/dataset/labels.jsonl).",
    )
    parser.add_argument(
        "--backend",
        default="heuristic",
        help="LLM backend for pre-fill predictions (default: heuristic).",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        default=True,
        help="Skip stable_ids already in the golden file (default: true).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Annotate at most N items per run.",
    )
    return parser.parse_args()


def load_dirty_items(dirty_path: Path) -> list[dict]:  # type: ignore
    paths = sorted(dirty_path.glob("*.jsonl")) if dirty_path.is_dir() else [dirty_path]

    items: list[dict] = []  # type: ignore
    for p in paths:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                with suppress(json.JSONDecodeError):
                    items.append(json.loads(line))
    return items


def load_golden_ids(golden_path: Path) -> set[str]:
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


async def prefill_predictions(raw_dict: dict[str, Any], backend: str) -> dict[str, Any]:
    """Run heuristic pipeline stages and return predicted field values."""
    try:
        from job_ftch.application.registry import create_llm
        from job_ftch.config import Settings
        from job_ftch.domain import RawItem
        from job_ftch.nodes.extraction import ExtractionNode
        from job_ftch.nodes.post_type import PostTypeClassificationNode

        settings = Settings(llm_backend=backend)
        raw_item = RawItem.model_validate(raw_dict)

        post_type_node = PostTypeClassificationNode()
        post_type_result = await post_type_node.process(raw_item)
        predicted_post_type = post_type_result.value if post_type_result is not None else "unknown"  # type: ignore

        llm = create_llm(settings)
        extractor = ExtractionNode(llm)  # type: ignore
        job = await extractor.process(raw_item)
        if job is not None:
            return {
                "post_type": predicted_post_type,
                "title": job.title or job.title_raw or "",  # type: ignore
                "company": job.company or "",  # type: ignore
                "seniority": job.seniority.value if job.seniority else "",
                "work_mode": job.work_mode.value if job.work_mode else "",
            }
        return {"post_type": predicted_post_type}
    except Exception as exc:
        return {"post_type": "unknown", "_prefill_error": str(exc)[:120]}


def _prompt(label: str, current: str) -> str:
    """Prompt user for a field value, returning current if empty."""
    val = input(f"  {label} [{current}]: ").strip()
    return val if val else current


def annotate_item(
    raw_dict: dict[str, Any], predictions: dict[str, Any], idx: int, total: int
) -> dict[str, Any] | None:
    """Present one item for annotation. Returns label dict or None to skip."""
    text = raw_dict.get("text", "")
    source = raw_dict.get("source_name", "?")
    url = raw_dict.get("url", "")
    stable_id = raw_dict.get("stable_id", "")

    print(f"\n{'─' * 70}")
    print(f"[{idx}/{total}]  source={source}  id={stable_id[:12]}")
    if url:
        print(f"  url: {url}")
    print()
    preview = textwrap.shorten(text, width=400, placeholder="…")
    for line in textwrap.wrap(preview, width=68):
        print(f"  {line}")
    print()
    print(
        f"  PREDICTIONS: post_type={predictions.get('post_type', '?')}  "
        f"title={predictions.get('title', '?')[:50]}  "
        f"company={predictions.get('company', '?')[:30]}  "
        f"seniority={predictions.get('seniority', '?')}  "
        f"work_mode={predictions.get('work_mode', '?')}"
    )
    print()
    print("  [Enter]=accept  [s]=skip  [r]=reject  [e]=edit fields  [q]=quit")

    while True:
        try:
            cmd = input("  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nInterrupted.")
            return None

        if cmd in ("", "a"):
            return {
                "labels": {
                    "post_type": predictions.get("post_type", ""),
                    "routing_decision": "ACCEPT",
                    "note": "",
                },
                "expected": {
                    "title": predictions.get("title", ""),
                    "company": predictions.get("company", ""),
                    "seniority": predictions.get("seniority", ""),
                    "work_mode": predictions.get("work_mode", ""),
                },
            }
        elif cmd == "s":
            return None
        elif cmd == "r":
            note = input("  rejection note (optional): ").strip()
            return {
                "labels": {
                    "post_type": predictions.get("post_type", ""),
                    "routing_decision": "REJECT",
                    "note": note,
                },
                "expected": {},
            }
        elif cmd == "e":
            post_type = _prompt("post_type", predictions.get("post_type", ""))
            routing = _prompt("routing_decision (ACCEPT/REVIEW/REJECT)", "ACCEPT")
            title = _prompt("title", predictions.get("title", ""))
            company = _prompt("company", predictions.get("company", ""))
            seniority = _prompt("seniority", predictions.get("seniority", ""))
            work_mode = _prompt("work_mode", predictions.get("work_mode", ""))
            note = _prompt("note", "")
            return {
                "labels": {
                    "post_type": post_type,
                    "routing_decision": routing.upper(),
                    "note": note,
                },
                "expected": {
                    "title": title,
                    "company": company,
                    "seniority": seniority,
                    "work_mode": work_mode,
                },
            }
        elif cmd == "q":
            print("Quitting annotation session.")
            sys.exit(0)
        else:
            print("  Unknown command. Use Enter/s/r/e/q.")


async def main() -> int:
    args = parse_args()

    dirty_path = Path(args.dirty)
    golden_path = Path(args.golden)
    golden_path.parent.mkdir(parents=True, exist_ok=True)

    dirty_items = load_dirty_items(dirty_path)
    if not dirty_items:
        print(f"No items found in {dirty_path}")
        return 0

    existing_ids = load_golden_ids(golden_path) if args.skip_existing else set()
    pending = [it for it in dirty_items if it.get("stable_id", "") not in existing_ids]

    if args.limit:
        pending = pending[: args.limit]

    print(
        f"Dirty items: {len(dirty_items)}  Already annotated: {len(existing_ids)}  Pending: {len(pending)}"
    )
    if not pending:
        print("Nothing to annotate.")
        return 0

    annotated = 0
    with golden_path.open("a", encoding="utf-8") as golden_fh:
        for idx, raw_dict in enumerate(pending, 1):
            predictions = await prefill_predictions(raw_dict, args.backend)
            result = annotate_item(raw_dict, predictions, idx, len(pending))
            if result is None:
                continue

            record = {
                "raw_item": raw_dict,
                "labels": result["labels"],
                "expected": result["expected"],
                "annotated_at": datetime.now(UTC).isoformat(),
                "annotator": "human",
            }
            golden_fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            golden_fh.flush()
            annotated += 1

    print(f"\nAnnotated {annotated} items → {golden_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
