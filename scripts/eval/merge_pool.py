"""Merge the clean eval pool with newly captured Telegram channel data."""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def _configure_utf8_stdio() -> None:
    if getattr(sys.stdout, "buffer", None) is not None:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if getattr(sys.stderr, "buffer", None) is not None:
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


_DEFAULT_NEW_SOURCES = (
    "tg_vakansii_rabotasd",
    "tg_fokina_jobs",
    "tg_odsjobs",
    "tg_ai_rabota",
    "tg_not_boring_ds_jobs",
    "tg_zerocode_jobs",
)
_ANTIBOT_RE = re.compile(
    r"(captcha|i am not a robot|я не робот|verify you are human)", re.IGNORECASE
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge clean pool with new dirty TG data.")
    parser.add_argument("--clean", default="fixtures/dataset/clean_pool.jsonl")
    parser.add_argument("--dirty-dir", default=".runtime/datasets/dirty")
    parser.add_argument("--new-sources", nargs="*", default=list(_DEFAULT_NEW_SOURCES))
    parser.add_argument("--out", default="fixtures/dataset/eval_dataset.jsonl")
    parser.add_argument(
        "--dirty-only",
        action="store_true",
        help="Build a fresh timestamped eval pool without mixing historical clean rows.",
    )
    return parser.parse_args()


def _iter_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"WARN: {path}:{line_no}: invalid JSON: {exc}", file=sys.stderr)
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _is_garbage_text(text: str) -> bool:
    stripped = text.strip()
    return len(stripped) < 30 or _ANTIBOT_RE.search(stripped) is not None


def _temporal_fields(row: dict[str, Any]) -> dict[str, str]:
    """Retain source observation times for a later time-aware promotion split."""
    return {
        field: value
        for field in ("fetched_at", "created_at")
        if isinstance((value := row.get(field)), str) and value.strip()
    }


def _normalize_clean_row(row: dict[str, Any]) -> dict[str, Any] | None:
    stable_id = str(row.get("stable_id", "")).strip()
    text = str(row.get("text", "") or "").strip()
    if not stable_id or not text or _is_garbage_text(text):
        return None
    return {
        "stable_id": stable_id,
        "source_kind": row.get("source_kind"),
        "source_name": row.get("source_name"),
        "url": row.get("url"),
        "text": text,
        "relevant": int(row.get("relevant", -1)) if row.get("relevant") is not None else -1,
        **_temporal_fields(row),
    }


def _normalize_dirty_row(row: dict[str, Any]) -> dict[str, Any] | None:
    stable_id = str(row.get("stable_id", "")).strip()
    text = str(row.get("text", "") or "").strip()
    if not stable_id or not text or _is_garbage_text(text):
        return None
    return {
        "stable_id": stable_id,
        "source_kind": row.get("source_kind"),
        "source_name": row.get("source_name"),
        "url": row.get("url"),
        "text": text,
        "relevant": -1,
        **_temporal_fields(row),
    }


def _collect_dirty_files(dirty_dir: Path, source_name: str) -> list[Path]:
    pattern = f"{source_name}_*.jsonl"
    return sorted(dirty_dir.glob(pattern))


def main() -> int:
    args = parse_args()
    clean_path = Path(args.clean)
    dirty_dir = Path(args.dirty_dir)
    out_path = Path(args.out)

    if not args.dirty_only and not clean_path.exists():
        print(f"ERROR: clean pool not found: {clean_path}", file=sys.stderr)
        return 1
    if not dirty_dir.exists():
        print(f"ERROR: dirty dir not found: {dirty_dir}", file=sys.stderr)
        return 1

    merged: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    stats: Counter[str] = Counter()
    by_source: Counter[str] = Counter()

    if not args.dirty_only:
        for row in _iter_jsonl(clean_path):
            normalized = _normalize_clean_row(row)
            if normalized is None:
                stats["clean_skipped"] += 1
                continue
            stable_id = str(normalized["stable_id"])
            if stable_id in seen_ids:
                stats["duplicates"] += 1
                continue
            seen_ids.add(stable_id)
            merged.append(normalized)
            stats["clean_kept"] += 1
            by_source[str(normalized.get("source_name") or "unknown")] += 1

    for source_name in args.new_sources:
        files = _collect_dirty_files(dirty_dir, source_name)
        if not files:
            print(f"WARN: no dirty files found for {source_name}", file=sys.stderr)
            continue
        for path in files:
            for row in _iter_jsonl(path):
                normalized = _normalize_dirty_row(row)
                if normalized is None:
                    stats["dirty_skipped"] += 1
                    continue
                stable_id = str(normalized["stable_id"])
                if stable_id in seen_ids:
                    stats["duplicates"] += 1
                    continue
                seen_ids.add(stable_id)
                merged.append(normalized)
                stats["dirty_kept"] += 1
                by_source[str(normalized.get("source_name") or "unknown")] += 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in merged) + "\n",
        encoding="utf-8",
    )

    print(f"Wrote {len(merged)} items to {out_path}")
    print(f"Stats: {dict(stats)}")
    print("Top sources:")
    for source_name, count in by_source.most_common(12):
        print(f"  {source_name}: {count}")
    return 0


if __name__ == "__main__":
    _configure_utf8_stdio()
    raise SystemExit(main())
