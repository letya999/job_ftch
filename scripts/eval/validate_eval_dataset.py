"""Validate an immutable JSONL relevance dataset and its append-only patches."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from job_ftch.application.dataset_hashing import dataset_hash

_ERROR_LABEL = re.compile(r"(?:^|\b)(?:error|timeout|429|rate limit|api failure)(?:\b|:)", re.I)


def content_hash(row: dict[str, Any]) -> str:
    text = " ".join(str(row.get("text", "")).split())
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: invalid JSON: {exc.msg}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{line_no}: row must be an object")
        rows.append(row)
    return rows


def _has_resolved_label_override(row: dict[str, Any]) -> bool:
    """Return whether labels are backed by review or a narrow invariant repair."""
    history = row.get("label_history")
    if not isinstance(history, list) or not history:
        return False
    latest = history[-1]
    if not isinstance(latest, dict):
        return False
    new = latest.get("new")
    matching_labels = (
        isinstance(new, dict)
        and new.get("is_job") == row.get("is_job")
        and new.get("relevant") == row.get("relevant")
    )
    if latest.get("status") == "adjudicated":
        return bool(latest.get("adjudicator")) and matching_labels
    if latest.get("status") != "deterministic_repair":
        return False
    if latest.get("repairer") != "eval-dataset-validator" or not matching_labels:
        return False
    if latest.get("repair_kind") == "provider_failure_label_unknown":
        return new == {"is_job": "unknown", "relevant": "unknown"}
    return latest.get("repair_kind") == "positive_requires_jobness" and new == {
        "is_job": 1,
        "relevant": 1,
    }


def has_adjudicated_positive_provenance(row: dict[str, Any]) -> bool:
    """Require a human-recorded latest review for a promotion positive label."""
    if row.get("relevant") != 1:
        return True
    history = row.get("label_history")
    if not isinstance(history, list) or not history or not isinstance(history[-1], dict):
        return False
    latest = history[-1]
    new = latest.get("new")
    return (
        latest.get("status") == "adjudicated"
        and bool(latest.get("adjudicator"))
        and isinstance(new, dict)
        and new.get("relevant") == 1
        and new.get("is_job") == 1
    )


def validate_rows(rows: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    ids: set[str] = set()
    duplicates: dict[str, list[str]] = defaultdict(list)
    for index, row in enumerate(rows, 1):
        stable_id = str(row.get("stable_id", "")).strip()
        if not stable_id:
            errors.append(f"row {index}: missing stable_id")
        elif stable_id in ids:
            errors.append(f"row {index}: duplicate stable_id {stable_id}")
        ids.add(stable_id)
        if not str(row.get("text", "")).strip():
            errors.append(f"row {index}: empty text")
        is_job, relevant = row.get("is_job"), row.get("relevant")
        if is_job not in (0, 1, "unknown", None) or relevant not in (0, 1, "unknown", None):
            errors.append(f"row {index}: labels must be 0, 1, unknown, or null")
        if relevant == 1 and is_job != 1:
            errors.append(f"row {index}: relevant=1 requires is_job=1")
        if (
            is_job == 0
            and _ERROR_LABEL.search(str(row.get("reason", "")))
            and not _has_resolved_label_override(row)
        ):
            errors.append(f"row {index}: provider failure must be unknown, not is_job=0")
        duplicates[content_hash(row)].append(stable_id or f"row-{index}")
    # Duplicates are allowed only if a split manifest keeps their group together.
    # Report them here so the caller can use the result in split construction.
    for members in duplicates.values():
        if len(members) > 1:
            errors.append(f"duplicate-content group: {', '.join(members)}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--strict", action="store_true", help="return non-zero for findings")
    args = parser.parse_args()
    rows = load_rows(args.dataset)
    findings = validate_rows(rows)
    report = {
        "dataset": str(args.dataset),
        "sha256": dataset_hash(args.dataset),
        "rows": len(rows),
        "findings": findings,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if args.strict and findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
