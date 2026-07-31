#!/usr/bin/env python3
"""Project structured site metadata onto records extracted before it took precedence.

`FullExtractionNode` used to resolve location as `llm or record or metadata`, so
an inference drawn from prose outranked the place the site itself published. A
Sberbank posting stored "офис рядом с м. Кутузовская" - lifted from its benefits
list - while metadata held "г Москва". Work mode consulted no metadata at all,
and the resolver ignored schema.org's `jobLocationType`, so postings publishing
TELECOMMUTE in JSON-LD were stored as unknown.

Tags were the third case: sites that publish one hand it over as structured
data, nothing consumed it, and moving hirify from scraping rendered chips to
reading its API removed the keyword list the LLM had been picking tools out of,
dropping tools_stack coverage on that source to zero.

The node now covers all three, but stored records keep whatever was written at
the time and `Pipeline.run` skips anything already processed. This backfill
re-applies the projection in place. It is deterministic - no LLM call - and
touches only `location`, `work_mode` and an empty `tools_stack`.

Usage:
    python scripts/backfill_metadata_projection.py --dry-run
    python scripts/backfill_metadata_projection.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_ftch.nodes.extraction import _fallback_work_mode_from_metadata
from job_ftch.nodes.full_extraction import (
    _first_metadata_location,
    _is_unusable_location,
    _metadata_skills,
)


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def repairs_for(record: dict[str, Any]) -> dict[str, str]:
    """Fields whose stored value disagrees with what metadata says."""
    metadata = record.get("metadata") or {}
    changes: dict[str, str] = {}

    # Same rule as the node: only rescue values that are not places. Metadata
    # is not reliably better - on hh.ru it carries the search scope, and applying
    # it unconditionally would have moved a German town to Moscow.
    stored_location = record.get("location")
    if _is_unusable_location(stored_location):
        metadata_location = _first_metadata_location(metadata)
        if metadata_location and metadata_location != stored_location:
            changes["location"] = metadata_location

    if str(record.get("work_mode") or "unknown") == "unknown":
        recovered = _fallback_work_mode_from_metadata(metadata)
        if recovered.value != "unknown":
            changes["work_mode"] = recovered.value

    # Purely additive: API-supplied tags only fill a stack the extraction left
    # empty. Sources whose tag list the LLM already read keep their own answer.
    if not record.get("tools_stack"):
        tags = _metadata_skills(metadata)
        if tags:
            changes["tools_stack"] = list(tags)

    return changes


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path, default=Path("metadata_backfill_backup.json"))
    args = parser.parse_args()

    load_env(Path(".env.prod"))
    dsn = os.environ.get("JOB_FTCH_STORE_DSN")
    if not dsn:
        raise SystemExit("JOB_FTCH_STORE_DSN is not set.")

    import asyncpg

    conn = await asyncpg.connect(dsn)
    rows = await conn.fetch("SELECT job_id, raw_json FROM jf_jobs")

    backup: list[dict[str, Any]] = []
    changed = 0
    for row in rows:
        record = json.loads(row["raw_json"])
        changes = repairs_for(record)
        if not changes:
            continue
        changed += 1
        for field, new_value in changes.items():
            print(f"  {row['job_id'][:12]} {field}: {record.get(field)!r} -> {new_value!r}")
        backup.append(
            {
                "job_id": row["job_id"],
                "location": record.get("location"),
                "work_mode": record.get("work_mode"),
                "tools_stack": record.get("tools_stack"),
            }
        )
        if args.apply:
            record.update(changes)
            await conn.execute(
                "UPDATE jf_jobs SET raw_json = $1, location = $2, updated_at = now() "
                "WHERE job_id = $3",
                json.dumps(record, ensure_ascii=False),
                record.get("location"),
                row["job_id"],
            )

    await conn.close()

    if args.apply and backup:
        args.backup.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\napplied to {changed}/{len(rows)} records; previous values in {args.backup}")
    else:
        print(f"\ndry run: {changed}/{len(rows)} records would change")


if __name__ == "__main__":
    asyncio.run(main())
