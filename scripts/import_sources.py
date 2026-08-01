"""Batch-register sources into a tenant store via the canonical add path.

Each input URL is turned into a typed ``SourceSpec`` with
``build_source_spec_from_input`` - the same typization the bot's ``/sources``
command uses - and then registered through ``TenantRunner.add_source_spec``,
which runs source assessment (``assess_and_store``) and persists a durable
``RuntimeSourceRecord``. Registration is therefore identical to adding a source
by hand in the bot, only batched.

Robustness:
- one unreachable/failed site never aborts the batch (each add is isolated);
- already-configured sources are skipped idempotently by ``source_id``;
- duplicate ``source_id`` values inside one input file are collapsed;
- ``--dry-run`` builds and prints the typed specs without touching the store.

Run it where the tenant store lives. In production that is inside the bot
container so it uses the same ``JOB_FTCH_STORE_DSN`` the scheduler uses::

    docker cp scripts/import_sources.py telegram_bot_prod-bot-1:/app/scripts/
    docker cp fixtures/sources/ai_jobs_cis_import_2026q3.yaml \
        telegram_bot_prod-bot-1:/app/fixtures/sources/
    docker exec telegram_bot_prod-bot-1 python scripts/import_sources.py \
        --tenant ai_jobs --file fixtures/sources/ai_jobs_cis_import_2026q3.yaml --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import re
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_ftch.application.source_inputs import build_source_spec_from_input
from job_ftch.cli import _load_tenant_runner
from job_ftch.config import get_settings
from job_ftch.domain import source_spec_identifier

_URL_RE = re.compile(r"https?://[^\s\)\]\"']+")


def _read_urls(path: Path) -> list[str]:
    """Extract one URL per non-comment line, preserving order and de-duplicating.

    Tolerates plain lines, YAML ``- url`` list items, and markdown ``[u](u)`` so
    the same file can be reused across tools without reformatting.
    """
    urls: list[str] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.lstrip("-# ").startswith("#") or line.startswith("#"):
            continue
        match = _URL_RE.search(line)
        if match is None:
            continue
        url = match.group(0).rstrip(".,;")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


async def _build_specs(
    urls: list[str], *, source_type: str, limit: int
) -> tuple[list[tuple[str, object]], int]:
    """Turn URLs into (source_id, spec) pairs, de-duplicating by source_id.

    Returns the built pairs and a count of URLs that could not be typed.
    """
    pairs: list[tuple[str, object]] = []
    seen_ids: set[str] = set()
    failed = 0
    for url in urls:
        try:
            spec = await build_source_spec_from_input(url, source_type=source_type, limit=limit)
        except Exception as exc:  # noqa: BLE001 - report and continue
            failed += 1
            print(f"SPEC_ERROR  {url}: {exc}")
            continue
        source_id = source_spec_identifier(spec)
        if source_id in seen_ids:
            print(f"SKIP  {source_id:48} (duplicate in input)  <- {url}")
            continue
        seen_ids.add(source_id)
        pairs.append((source_id, spec))
    return pairs, failed


async def _run(args: argparse.Namespace) -> int:
    urls = _read_urls(Path(args.file))
    if not urls:
        print(f"ERROR: no URLs found in {args.file}")
        return 2

    print(
        f"Importing {len(urls)} source(s) into tenant '{args.tenant}' "
        f"(dry_run={args.dry_run}, enabled={not args.disabled})\n"
    )
    pairs, spec_failed = await _build_specs(urls, source_type=args.source_type, limit=args.limit)

    # Dry-run validates typization only and needs no store or runtime settings,
    # so it can be executed anywhere (host or container) as a pre-flight check.
    if args.dry_run:
        for source_id, spec in pairs:
            print(f"DRY   {source_id:48} name={spec.source_name}")  # type: ignore[attr-defined]
        print(
            f"\nSUMMARY tenant={args.tenant} total={len(urls)} "
            f"typed={len(pairs)} spec_errors={spec_failed} dry_run=True"
        )
        return 1 if (args.strict and spec_failed) else 0

    settings = get_settings()
    runner = _load_tenant_runner(settings)
    known = runner.tenant_ids()
    if args.tenant not in known:
        print(f"ERROR: unknown tenant '{args.tenant}'. Known tenants: {known}")
        with contextlib.suppress(Exception):
            await runner.close()
        return 2

    added = 0
    skipped = 0
    failed = spec_failed
    try:
        for source_id, spec in pairs:
            try:
                payload = await runner.add_source_spec(
                    args.tenant, spec, added_via="batch_import", input_value=str(source_id)
                )
            except ValueError as exc:  # already configured
                skipped += 1
                print(f"SKIP  {source_id:48} ({exc})")
                continue
            except Exception as exc:  # noqa: BLE001 - one bad source must not abort
                failed += 1
                print(f"FAIL  {source_id:48} {type(exc).__name__}: {exc}")
                continue

            if args.disabled:
                with contextlib.suppress(Exception):
                    await runner.disable_source(args.tenant, source_id)

            assessment = payload.get("assessment") or {}
            status = assessment.get("status", "unknown")
            confidence = assessment.get("confidence", "-")
            added += 1
            print(f"ADD   {source_id:48} assess={status}/{confidence} enabled={not args.disabled}")
    finally:
        with contextlib.suppress(Exception):
            await runner.close()

    print(
        f"\nSUMMARY tenant={args.tenant} total={len(urls)} "
        f"added={added} skipped={skipped} failed={failed} dry_run=False"
    )
    if args.strict and failed:
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default="ai_jobs", help="Target tenant id.")
    parser.add_argument(
        "--file", required=True, help="Path to a URL list (plain / YAML list / markdown)."
    )
    parser.add_argument(
        "--source-type",
        default="auto",
        help="Source type hint passed to the spec builder (auto|career_site|telegram|rss).",
    )
    parser.add_argument("--limit", type=int, default=100, help="Per-source item limit.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build and print typed specs without writing to the store.",
    )
    parser.add_argument(
        "--disabled",
        action="store_true",
        help="Register the sources but leave them disabled (assessment still runs).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any source failed to register.",
    )
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
