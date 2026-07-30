"""Convert full-text source audit reports into an immutable eval JSONL dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-report", type=Path, action="append", required=True)
    parser.add_argument("--telegram-report", type=Path, required=True)
    parser.add_argument("--labels", type=Path, action="append", default=[])
    parser.add_argument("--labeler", default="codex_manual_adjudication_20260717")
    parser.add_argument("--out", type=Path, required=True)
    return parser


def _rows(path: Path) -> list[dict[str, Any]]:
    report = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for source in report.get("results", []):
        source_id = str(source.get("source_id") or "")
        source_kind, _, fallback_name = source_id.partition(":")
        for item in source.get("items", []):
            text = str(item.get("text") or "")
            stable_id = str(item.get("stable_id") or "")
            if not stable_id or not text:
                raise ValueError(f"Incomplete audit item in {path}: {source_id}")
            rows.append(
                {
                    "stable_id": stable_id,
                    "source_kind": source_kind,
                    "source_name": str(item.get("source_name") or fallback_name),
                    "source_id": source_id,
                    "url": item.get("url"),
                    "text": text,
                    "created_at": item.get("created_at") or None,
                    "metadata": {
                        "preclassified_post_type": item.get("post_type"),
                        "preclassified_model": item.get("post_type_model"),
                        "source_family": item.get("source_family"),
                        "observation_kind": item.get("observation_kind"),
                        "transport": item.get("transport"),
                        "detail_vacancy_confirmed": item.get("detail_vacancy_confirmed"),
                        "date_posted": item.get("date_posted"),
                    },
                }
            )
    return rows


def _labels(paths: list[Path]) -> dict[str, dict[str, Any]]:
    labels: dict[str, dict[str, Any]] = {}
    for path in paths:
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            stable_id = str(row.get("stable_id") or "")
            if not stable_id or stable_id in labels:
                raise ValueError(f"Missing or duplicate label at {path}:{line_no}")
            is_job = row.get("is_job")
            relevant = row.get("relevant")
            if type(is_job) is not int or is_job not in {0, 1}:  # noqa: E721
                raise ValueError(f"is_job must be integer 0/1 at {path}:{line_no}")
            if type(relevant) is not int or relevant not in {0, 1}:  # noqa: E721
                raise ValueError(f"relevant must be integer 0/1 at {path}:{line_no}")
            if relevant and not is_job:
                raise ValueError(f"Non-job cannot be relevant at {path}:{line_no}")
            if not str(row.get("reason") or "").strip():
                raise ValueError(f"Missing reason at {path}:{line_no}")
            labels[stable_id] = row
    return labels


def main() -> int:
    args = _parser().parse_args()
    site_rows = [row for path in args.site_report for row in _rows(path)]
    rows = [*site_rows, *_rows(args.telegram_report)]
    seen: set[str] = set()
    duplicates: list[str] = []
    for row in rows:
        stable_id = row["stable_id"]
        if stable_id in seen:
            duplicates.append(stable_id)
        seen.add(stable_id)
    if duplicates:
        raise ValueError(f"Duplicate stable IDs in snapshot: {sorted(set(duplicates))}")

    labels = _labels(args.labels)
    if labels:
        row_ids = {row["stable_id"] for row in rows}
        missing = row_ids - labels.keys()
        extra = labels.keys() - row_ids
        if missing or extra:
            raise ValueError(f"Label coverage mismatch: missing={len(missing)}, extra={len(extra)}")
        for row in rows:
            label = labels[row["stable_id"]]
            row.update(
                {
                    "is_job": label["is_job"],
                    "relevant": label["relevant"],
                    "reason": label["reason"],
                    "labeler": args.labeler,
                    "label_history": [
                        {
                            "status": "adjudicated",
                            "adjudicator": args.labeler,
                            "new": {
                                "is_job": label["is_job"],
                                "relevant": label["relevant"],
                            },
                            "reason": label["reason"],
                        }
                    ],
                }
            )

    payload = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(payload, encoding="utf-8", newline="\n")
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    print(json.dumps({"rows": len(rows), "sha256": digest, "out": str(args.out)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
