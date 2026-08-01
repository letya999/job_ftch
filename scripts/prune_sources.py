"""Deduplicate tenant sources by host, then re-probe/re-assess the survivors.

For each host with more than one enabled ``career_site`` source, keep one
canonical source (prefer a base-config source, then the barest URL - no query,
shortest path) and disable the rest through ``TenantRunner.disable_source``.
Then force a fresh source assessment (a live re-probe) for every surviving
enabled career_site source, so freshly normalised bare URLs get re-evaluated.

Idempotent and safe to re-run. Run inside the prod bot container so it uses the
same store the scheduler uses::

    docker cp scripts/prune_sources.py telegram_bot_prod-bot-1:/app/scripts/
    docker exec telegram_bot_prod-bot-1 python scripts/prune_sources.py --tenant ai_jobs --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_ftch.application.source_assessment import create_source_assessment_service
from job_ftch.application.source_inputs import build_source_spec_from_input
from job_ftch.cli import _load_tenant_runner
from job_ftch.config import get_settings


def _host(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def _key(url: str) -> tuple[str, str]:
    """Dedup key: host + normalised path (query ignored).

    Grouping by host alone would merge genuinely distinct pages on shared hosts
    (e.g. career.habr.com/vacancies vs career.habr.com/companies/rwb/vacancies),
    so the path is part of the key; only the query - which expansion regenerates -
    is dropped.
    """
    parsed = urlparse(url)
    return _host(url), parsed.path.rstrip("/").lower()


def _rank(source: dict) -> tuple:
    """Canonical-source order: config source first, then barest URL, then id."""
    url = str(source.get("spec", {}).get("url", ""))
    parsed = urlparse(url)
    return (
        0 if source.get("origin") in ("config", "base") else 1,
        1 if parsed.query else 0,
        len(parsed.path),
        str(source.get("source_id", "")),
    )


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    runner = _load_tenant_runner(settings)
    if args.tenant not in runner.tenant_ids():
        print(f"ERROR: unknown tenant '{args.tenant}'. Known: {runner.tenant_ids()}")
        return 2

    sources = await runner.list_sources(args.tenant)
    career = [s for s in sources if s.get("source_kind") == "career_site"]
    by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for source in career:
        by_key[_key(str(source.get("spec", {}).get("url", "")))].append(source)

    disabled = 0
    survivors: list[dict] = []
    try:
        for key, group in sorted(by_key.items()):
            enabled = [s for s in group if s.get("enabled")]
            if len(enabled) <= 1:
                survivors.extend(enabled)
                continue
            enabled.sort(key=_rank)
            keep, *drop = enabled
            survivors.append(keep)
            print(f"{key[0]}{key[1]}: keep {keep['source_id']} ({keep['origin']})")
            for source in drop:
                url = source.get("spec", {}).get("url", "")
                if args.dry_run:
                    print(f"   would disable {source['source_id']} ({source['origin']}) {url}")
                    continue
                try:
                    await runner.disable_source(args.tenant, source["source_id"])
                    disabled += 1
                    print(f"   disabled {source['source_id']}")
                except Exception as exc:  # noqa: BLE001 - never abort the batch
                    print(f"   FAIL disable {source['source_id']}: {exc}")

        reassessed = 0
        if not args.dry_run and not args.no_reassess:
            service = create_source_assessment_service()
            store = runner.get_runtime(args.tenant).store
            ttl = runner.get_runtime(args.tenant).settings.source_assessment_ttl_days
            for source in survivors:
                url = str(source.get("spec", {}).get("url", ""))
                try:
                    spec = await build_source_spec_from_input(url, source_type="career_site")
                    await service.assess_and_store(spec, store, force=True, ttl_days=ttl)
                    reassessed += 1
                    print(f"REASSESS {source['source_id']}")
                except Exception as exc:  # noqa: BLE001 - one dead site must not abort
                    print(f"   FAIL reassess {source['source_id']}: {exc}")
    finally:
        with contextlib.suppress(Exception):
            await runner.close()

    print(
        f"\nSUMMARY tenant={args.tenant} career_sources={len(career)} "
        f"hosts={len(by_key)} disabled={disabled} "
        f"survivors={len(survivors)} dry_run={args.dry_run}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default="ai_jobs")
    parser.add_argument("--dry-run", action="store_true", help="Only report what would change.")
    parser.add_argument(
        "--no-reassess", action="store_true", help="Skip the re-probe/re-assess pass."
    )
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
