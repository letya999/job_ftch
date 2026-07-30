"""Clean the raw item pool for evaluation dataset construction.

Reads the existing raw items from ``fixtures/dataset/labels.jsonl`` (we reuse the
real scraped texts, not the new node-coupled labels) and strips garbage that must
never count as a relevant vacancy:

- antibot / captcha interstitials ("Подтвердите, что вы не робот", Cloudflare, ...)
- effectively empty posts (no labelable content at all)

Short, casual posts (e.g. Telegram-group chatter) are deliberately KEPT: for a
binary relevant/not eval they are valid *negative* examples the pipeline must
reject, so removing them would bias the dataset toward positives and starve the
group source of data.

Output: ``fixtures/dataset/clean_pool.jsonl`` with one raw_item per line plus a
``_clean`` block recording why the item was kept. A companion report is printed
to stdout. Garbage items are written to ``fixtures/dataset/garbage_pool.jsonl``
with the drop reason so the decision is auditable.

Usage:
    python scripts/eval/clean_pool.py
"""

from __future__ import annotations

import io
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_SRC = Path("fixtures/dataset/labels.jsonl")
_CLEAN_OUT = Path("fixtures/dataset/clean_pool.jsonl")
_GARBAGE_OUT = Path("fixtures/dataset/garbage_pool.jsonl")

_ANTIBOT_RE = re.compile(
    r"(?:подтвердите,?\s*что\s*вы\s*не\s*робот|вы\s*не\s*робот|"
    r"i'?m not a robot|verify you are human|are you a human|"
    r"enable javascript|включите javascript|cloudflare|captcha|"
    r"too many requests|access denied|доступ ограничен)",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-я0-9]+")

# Only truly content-free posts are dropped. Short chatter is kept as a negative.
_MIN_CHARS = 10
_MIN_TOKENS = 2


def _garbage_reason(text: str) -> str | None:
    stripped = text.strip()
    if _ANTIBOT_RE.search(stripped):
        return "antibot_captcha"
    if len(stripped) < _MIN_CHARS or len(_TOKEN_RE.findall(stripped)) < _MIN_TOKENS:
        return "empty_or_nearly_empty"
    return None


def main() -> int:
    if not _SRC.exists():
        print(f"ERROR: {_SRC} not found", file=sys.stderr)
        return 1

    rows = [
        json.loads(line) for line in _SRC.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    kept: list[dict] = []  # type: ignore
    dropped: list[dict] = []  # type: ignore
    by_kind_kept: Counter[str] = Counter()
    by_reason: Counter[str] = Counter()

    seen_ids: set[str] = set()
    for row in rows:
        raw = row.get("raw_item", {})
        text = raw.get("text", "") or ""
        sid = raw.get("stable_id", "")
        reason = _garbage_reason(text)
        if sid and sid in seen_ids:
            reason = reason or "duplicate_stable_id"
        if reason is not None:
            dropped.append(
                {
                    "stable_id": sid,
                    "source_kind": raw.get("source_kind"),
                    "source_name": raw.get("source_name"),
                    "reason": reason,
                }
            )
            by_reason[reason] += 1
            continue
        seen_ids.add(sid)
        kept.append(raw)
        by_kind_kept[raw.get("source_kind", "?")] += 1

    _CLEAN_OUT.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in kept) + "\n", encoding="utf-8"
    )
    _GARBAGE_OUT.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in dropped) + "\n", encoding="utf-8"
    )

    print(f"Raw pool: {len(rows)} -> kept {len(kept)} | dropped {len(dropped)}")
    print("Dropped by reason:", dict(by_reason))
    print("Kept by source_kind:", dict(by_kind_kept))
    print(f"Wrote {_CLEAN_OUT} and {_GARBAGE_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
