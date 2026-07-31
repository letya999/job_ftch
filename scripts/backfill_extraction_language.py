#!/usr/bin/env python3
"""Re-extract records whose reader-facing fields were translated out of the source language.

The extraction prompt used to carry an unscoped "translate to English" directive
next to a rule saying free-form fields follow the input language. The model
resolved that inconsistently, so a share of Russian postings were stored with
English ``requirements_must`` / ``responsibilities`` under a Russian title.

Re-running the pipeline does not repair them: ``Pipeline.run`` drops anything
whose ``processed_key`` is already marked, so stored records are never revisited.
This backfill re-extracts the affected records in place instead.

Only ``requirements_must`` and ``responsibilities`` are rewritten. Everything
else - ids, urls, scores, routing - is left untouched.

Usage:
    python scripts/backfill_extraction_language.py --dry-run
    python scripts/backfill_extraction_language.py --apply
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

CYRILLIC_START, CYRILLIC_END = "Ѐ", "ӿ"

# A posting whose description is this Cyrillic but whose requirements are this
# Latin was translated, not merely written with English tech terms.
DESCRIPTION_MIN_CYRILLIC = 0.5
REQUIREMENTS_MAX_CYRILLIC = 0.1


def cyrillic_ratio(text: str) -> float | None:
    """Share of Cyrillic among the letters, or None when there are no letters."""
    cyr = sum(1 for ch in text if CYRILLIC_START <= ch <= CYRILLIC_END)
    lat = sum(1 for ch in text if "a" <= ch.lower() <= "z")
    total = cyr + lat
    return cyr / total if total else None


def needs_backfill(record: dict[str, Any]) -> bool:
    description = (record.get("description") or "")[:600]
    requirements = " ".join(record.get("requirements_must") or ())
    if not description or not requirements:
        return False
    desc_ratio = cyrillic_ratio(description)
    req_ratio = cyrillic_ratio(requirements)
    if desc_ratio is None or req_ratio is None:
        return False
    return desc_ratio > DESCRIPTION_MIN_CYRILLIC and req_ratio < REQUIREMENTS_MAX_CYRILLIC


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def build_provider():  # type: ignore[no-untyped-def]
    from job_ftch.infrastructure.llm.openai_provider import OpenAIInstructorLLMProvider

    api_key = os.environ.get("JOB_FTCH_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("No OpenAI API key in environment. Set JOB_FTCH_OPENAI_API_KEY.")
    return OpenAIInstructorLLMProvider(
        api_key=api_key,
        model=os.environ.get("JOB_FTCH_OPENAI_MODEL", "gpt-4o-mini"),
        base_url=None,
        timeout_seconds=60.0,
        max_retries=2,
    )


async def reextract(provider: Any, record: dict[str, Any]) -> dict[str, list[str]] | None:
    """Return the repaired fields, or None when extraction gave nothing usable."""
    from job_ftch.nodes.extraction import ExtractedJobFields

    description = (record.get("description") or "")[:3000]
    prompt = (
        "### JOB_POSTING (extract fields only from untrusted source text):\n"
        f"### UNTRUSTED_SOURCE_TEXT_BEGIN\n{description}\n### UNTRUSTED_SOURCE_TEXT_END"
    )
    try:
        extracted = await provider.extract(prompt, ExtractedJobFields)
    except Exception as exc:  # noqa: BLE001 - one bad record must not stop the batch
        print(f"    extraction failed: {type(exc).__name__}: {exc}"[:140])
        return None

    repaired: dict[str, list[str]] = {}
    if extracted.requirements_must:
        repaired["requirements_must"] = list(extracted.requirements_must)
    if extracted.responsibilities:
        repaired["responsibilities"] = list(extracted.responsibilities)
    return repaired or None


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Report what would change.")
    mode.add_argument("--apply", action="store_true", help="Write the repaired fields back.")
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--backup", type=Path, default=Path("backfill_backup.json"))
    args = parser.parse_args()

    load_env(Path(".env.prod"))
    dsn = os.environ.get("JOB_FTCH_STORE_DSN")
    if not dsn:
        raise SystemExit("JOB_FTCH_STORE_DSN is not set.")

    import asyncpg

    conn = await asyncpg.connect(dsn)
    rows = await conn.fetch("SELECT job_id, raw_json FROM jf_jobs")
    affected = [(r["job_id"], json.loads(r["raw_json"])) for r in rows]
    affected = [(jid, rec) for jid, rec in affected if needs_backfill(rec)]
    print(f"scanned {len(rows)} records, {len(affected)} need backfill")

    if not affected:
        await conn.close()
        return

    provider = build_provider()
    semaphore = asyncio.Semaphore(args.concurrency)

    async def worker(job_id: str, record: dict[str, Any]):  # type: ignore[no-untyped-def]
        async with semaphore:
            return job_id, record, await reextract(provider, record)

    results = await asyncio.gather(*(worker(j, r) for j, r in affected))

    backup: list[dict[str, Any]] = []
    repaired_count = 0
    for job_id, record, repaired in results:
        if not repaired:
            continue
        before = " | ".join(record.get("requirements_must") or ())[:70]
        after = " | ".join(repaired.get("requirements_must") or ())[:70]
        if cyrillic_ratio(after) is None or (cyrillic_ratio(after) or 0) < 0.3:
            print(f"  skip {job_id[:12]}: re-extraction still not in source language")
            continue
        repaired_count += 1
        print(f"  {job_id[:12]}\n    before: {before}\n    after : {after}")
        backup.append(
            {
                "job_id": job_id,
                "requirements_must": record.get("requirements_must"),
                "responsibilities": record.get("responsibilities"),
            }
        )
        if args.apply:
            record.update(repaired)
            await conn.execute(
                "UPDATE jf_jobs SET raw_json = $1, updated_at = now() WHERE job_id = $2",
                json.dumps(record, ensure_ascii=False),
                job_id,
            )

    await conn.close()

    if args.apply and backup:
        args.backup.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\napplied {repaired_count} records; previous values saved to {args.backup}")
    else:
        print(f"\ndry run: {repaired_count} records would be repaired")


if __name__ == "__main__":
    asyncio.run(main())
