"""Persist an ordered, binary-labelled eval sample for exact replay."""

from __future__ import annotations

import argparse
from pathlib import Path

from scripts.eval.run_pipeline_eval import (
    _has_binary_relevance_label,
    _load_selected_item_ids,
    _read_jsonl,
    _select_labeled_rows,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--sample", type=int)
    selector.add_argument("--from-selected-item-ids", type=Path)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    dataset_rows = _read_jsonl(args.dataset)
    dropped_unknown = 0
    if args.from_selected_item_ids is not None:
        requested = _load_selected_item_ids(args.from_selected_item_ids)
        by_id = {str(row.get("stable_id", "")): row for row in dataset_rows}
        missing = [stable_id for stable_id in requested if stable_id not in by_id]
        if missing:
            raise ValueError(
                "source selected IDs are missing from dataset: " + ", ".join(missing[:5])
            )
        rows = [
            by_id[stable_id]
            for stable_id in requested
            if _has_binary_relevance_label(by_id[stable_id])
        ]
        dropped_unknown = len(requested) - len(rows)
    else:
        if args.sample is None or args.sample <= 0:
            raise ValueError("--sample must be positive")
        rows = _select_labeled_rows(
            dataset_rows,
            sample=args.sample,
            seed=args.seed,
            full=False,
            selected_item_ids=None,
        )
    selected_ids = [str(row.get("stable_id", "")) for row in rows]
    if not all(selected_ids):
        raise ValueError("selected dataset row lacks stable_id")
    args.output.write_text("\n".join(selected_ids) + "\n", encoding="utf-8")
    print(
        f"wrote {len(selected_ids)} stable IDs to {args.output} "
        f"(dropped_nonbinary={dropped_unknown})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
