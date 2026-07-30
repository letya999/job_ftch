"""Create a deterministic source/content-group-aware evaluation split manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

_TIME_FIELDS = ("observed_at", "collected_at", "fetched_at", "published_at", "created_at")
_PROMOTION_SPLITS = ("train", "validation", "holdout")


def _content_hash(row: dict[str, Any]) -> str:
    text = " ".join(str(row.get("text", "")).split()).casefold()
    return hashlib.sha256(text.encode()).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_group(row: dict[str, Any]) -> str:
    kind = " ".join(str(row.get("source_kind") or "unknown").split()).casefold()
    name = " ".join(str(row.get("source_name") or "unknown").split()).casefold()
    return f"{kind}/{name}"


def _split_for_group(group_hash: str) -> str:
    bucket = int(group_hash[:8], 16) % 100
    if bucket < 60:
        return "train"
    if bucket < 80:
        return "validation"
    return "holdout"


class _UnionFind:
    def __init__(self) -> None:
        self._parents: dict[str, str] = {}

    def find(self, value: str) -> str:
        parent = self._parents.setdefault(value, value)
        if parent != value:
            self._parents[value] = self.find(parent)
        return self._parents[value]

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self._parents[right_root] = left_root


def _load_rows(dataset: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in dataset.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _stable_id(row: dict[str, Any], index: int) -> str:
    return str(row.get("stable_id") or f"row-{index}")


def _temporal_partitioning(rows: list[dict[str, Any]]) -> dict[str, Any]:
    present = [field for field in _TIME_FIELDS if any(row.get(field) for row in rows)]
    return {
        "status": "available" if present else "unavailable",
        "fields": present,
        "missing_row_count": sum(not any(row.get(field) for field in _TIME_FIELDS) for row in rows),
    }


def build_manifest(dataset: Path, regression_ids: set[str]) -> dict[str, Any]:
    rows = _load_rows(dataset)
    content_members: dict[str, list[str]] = defaultdict(list)
    row_by_id: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows, 1):
        stable_id = _stable_id(row, index)
        row_by_id[stable_id] = row
        content_members[_content_hash(row)].append(stable_id)

    regression = {
        stable_id
        for members in content_members.values()
        if regression_ids.intersection(members)
        for stable_id in members
    }
    groups = _UnionFind()
    for stable_id, row in row_by_id.items():
        if stable_id in regression:
            continue
        groups.union(f"content:{_content_hash(row)}", f"source:{_source_group(row)}")

    component_members: dict[str, list[str]] = defaultdict(list)
    component_nodes: dict[str, set[str]] = defaultdict(set)
    for stable_id, row in row_by_id.items():
        if stable_id not in regression:
            component = groups.find(f"content:{_content_hash(row)}")
            component_members[component].append(stable_id)
            component_nodes[component].update(
                {f"content:{_content_hash(row)}", f"source:{_source_group(row)}"}
            )

    membership = {stable_id: "regression" for stable_id in regression}
    for component, members in component_members.items():
        digest = hashlib.sha256("\n".join(sorted(component_nodes[component])).encode()).hexdigest()
        membership.update({stable_id: _split_for_group(digest) for stable_id in members})
    split_ids = {
        split: sorted(stable_id for stable_id, assigned in membership.items() if assigned == split)
        for split in ("regression", *_PROMOTION_SPLITS)
    }
    return {
        "schema_version": "eval-splits/v2",
        "dataset_sha256": _file_hash(dataset),
        "content_group_count": len(content_members),
        "source_group_count": len({_source_group(row) for row in rows}),
        "splits": split_ids,
        "split_id_hashes": {
            split: hashlib.sha256("\n".join(ids).encode()).hexdigest()
            for split, ids in split_ids.items()
        },
        "temporal_partitioning": _temporal_partitioning(rows),
    }


def regression_ids_from_sample(dataset: Path, *, sample_size: int, seed: int) -> set[str]:
    rows = _load_rows(dataset)
    if sample_size < 1:
        raise ValueError("regression sample size must be positive")
    selected = random.Random(seed).sample(rows, min(sample_size, len(rows)))
    return {_stable_id(row, index) for index, row in enumerate(selected, 1)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--regression-ids", type=Path)
    parser.add_argument("--regression-sample-size", type=int)
    parser.add_argument("--regression-seed", type=int, default=42)
    parser.add_argument("--require-temporal-partition", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.regression_ids and args.regression_sample_size:
        raise SystemExit("use either --regression-ids or --regression-sample-size")
    regression_ids = set()
    if args.regression_ids:
        regression_ids = {
            line.strip()
            for line in args.regression_ids.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    elif args.regression_sample_size:
        regression_ids = regression_ids_from_sample(
            args.dataset, sample_size=args.regression_sample_size, seed=args.regression_seed
        )
    manifest = build_manifest(args.dataset, regression_ids)
    if (
        args.require_temporal_partition
        and manifest["temporal_partitioning"]["status"] != "available"
    ):
        raise SystemExit("dataset has no supported observation timestamp for a temporal partition")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
