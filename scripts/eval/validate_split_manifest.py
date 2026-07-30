"""Validate a deterministic grouped eval split manifest against its dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from scripts.eval.build_split_manifest import _content_hash, _file_hash, _source_group
from scripts.eval.validate_eval_dataset import (
    has_adjudicated_positive_provenance,
    load_rows,
    validate_rows,
)

_PROMOTION_SPLITS = {"train", "validation", "holdout"}


def validate(
    dataset: Path,
    manifest_path: Path,
    *,
    require_clean_labels: bool = False,
    require_temporal_partition: bool = False,
    require_adjudicated_positive_provenance: bool = False,
) -> list[str]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    if manifest.get("schema_version") != "eval-splits/v2":
        errors.append("unsupported split manifest schema")
    if manifest.get("dataset_sha256") != _file_hash(dataset):
        errors.append("dataset hash does not match manifest")
    memberships: dict[str, str] = {}
    for split, ids in dict(manifest.get("splits") or {}).items():
        for stable_id in ids:
            previous = memberships.get(stable_id)
            if previous is not None and previous != split:
                errors.append(f"stable id belongs to multiple splits: {stable_id}")
            memberships[stable_id] = split
    groups: dict[str, set[str]] = defaultdict(set)
    source_groups: dict[str, set[str]] = defaultdict(set)
    rows = load_rows(dataset)
    for index, row in enumerate(rows, 1):
        stable_id = str(row.get("stable_id") or f"row-{index}")
        split = memberships.get(stable_id)
        if split is None:
            errors.append(f"missing split membership: {stable_id}")
            continue
        groups[_content_hash(row)].add(split)
        if split in _PROMOTION_SPLITS:
            source_groups[_source_group(row)].add(split)
    for group_hash, splits in groups.items():
        if len(splits) != 1:
            errors.append(f"content group crosses splits: {group_hash}")
    for source_group, splits in source_groups.items():
        if len(splits) != 1:
            errors.append(f"source group crosses promotion splits: {source_group}")
    for split, ids in dict(manifest.get("splits") or {}).items():
        expected = hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()
        if dict(manifest.get("split_id_hashes") or {}).get(split) != expected:
            errors.append(f"split id hash mismatch: {split}")
    if require_clean_labels:
        errors.extend(
            finding
            for finding in validate_rows(rows)
            if not finding.startswith("duplicate-content group:")
        )
    if require_adjudicated_positive_provenance:
        errors.extend(
            f"positive lacks adjudicated provenance: {row.get('stable_id', f'row-{index}')}".rstrip()
            for index, row in enumerate(rows, 1)
            if not has_adjudicated_positive_provenance(row)
        )
    temporal = dict(manifest.get("temporal_partitioning") or {})
    if require_temporal_partition and temporal.get("status") != "available":
        errors.append("temporal partitioning is unavailable")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--require-clean-labels", action="store_true")
    parser.add_argument("--require-temporal-partition", action="store_true")
    parser.add_argument("--require-adjudicated-positive-provenance", action="store_true")
    args = parser.parse_args()
    errors = validate(
        args.dataset,
        args.manifest,
        require_clean_labels=args.require_clean_labels,
        require_temporal_partition=args.require_temporal_partition,
        require_adjudicated_positive_provenance=args.require_adjudicated_positive_provenance,
    )
    if errors:
        raise SystemExit("\n".join(errors))
    print("split manifest OK")


if __name__ == "__main__":
    main()
