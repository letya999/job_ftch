"""Bypass tier benchmark — measures OK/Blocked/Captcha/Timeout per tier per domain.

NOT for CI: requires network access, proxies, and real target sites.
Run manually:
    python scripts/bench_bypass_tiers.py

Results saved to data/tier_benchmark_YYYYMMDD.json.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import structlog
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_ftch.application.registry import resolve_bypass  # noqa: E402
from job_ftch.infrastructure.bypass.failure_signal import HeuristicFailureSignal  # noqa: E402

logger = structlog.get_logger("bench_bypass_tiers")

TIERS_TO_BENCH = ("curl_stealth", "nodriver", "stealth_browser", "cloak")
TIMEOUT_SECONDS = 30.0


def _load_target_urls() -> list[str]:
    """Load career site URLs from fixtures."""
    fixture = Path("fixtures/sources/career_sites_cis_303.yaml")
    if not fixture.exists():
        logger.error("fixture_not_found", path=str(fixture))
        return []
    data = yaml.safe_load(fixture.read_text(encoding="utf-8"))
    urls: list[str] = []
    if isinstance(data, list):
        for item in data[:60]:
            if isinstance(item, str):
                urls.append(item)
            elif isinstance(item, dict) and "url" in item:
                urls.append(item["url"])
    return urls


async def _probe_tier_http(tier_name: str, url: str) -> dict[str, str]:
    """Probe a URL with a given bypass tier via HTTP."""
    try:
        tier = resolve_bypass(tier_name)
    except (ValueError, KeyError):
        return {"url": url, "tier": tier_name, "result": "unavailable"}

    signal = HeuristicFailureSignal()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            patched = await tier.apply_http(client)
            target = patched if patched is not None else client
            if hasattr(target, "get"):
                resp = await target.get(url, follow_redirects=True)
            else:
                resp = await client.get(url, follow_redirects=True)
            kind = signal.classify(
                status_code=resp.status_code,
                body=resp.content[:2000] if resp.content else b"",
                error=None,
            )
            return {
                "url": url,
                "tier": tier_name,
                "result": kind or "ok",
                "status": resp.status_code,
            }
    except httpx.TimeoutException:
        return {"url": url, "tier": tier_name, "result": "timeout"}
    except Exception as exc:
        return {"url": url, "tier": tier_name, "result": "error", "error": str(exc)[:100]}


async def main() -> None:
    urls = _load_target_urls()
    if not urls:
        print("No URLs to benchmark. Check fixtures/sources/career_sites_cis_303.yaml")
        return

    print(f"Benchmarking {len(urls)} URLs across {len(TIERS_TO_BENCH)} tiers...")
    results: list[dict[str, Any]] = []
    start = time.time()

    for tier_name in TIERS_TO_BENCH:
        print(f"\n--- Tier: {tier_name} ---")
        for i, url in enumerate(urls):
            result = await _probe_tier_http(tier_name, url)
            results.append(result)
            status = result.get("result", "?")
            print(f"  [{i + 1}/{len(urls)}] {status:10s} {url[:60]}")

    elapsed = time.time() - start
    print(f"\nDone in {elapsed:.1f}s")

    matrix: dict[str, dict[str, int]] = {}
    for r in results:
        tier = r["tier"]
        outcome = r["result"]
        if tier not in matrix:
            matrix[tier] = {
                "ok": 0,
                "blocked": 0,
                "captcha": 0,
                "timeout": 0,
                "error": 0,
                "unavailable": 0,
            }
        bucket = outcome if outcome in matrix[tier] else "error"
        matrix[tier][bucket] += 1

    print("\n=== MATRIX ===")
    print(f"{'Tier':<18} {'OK':>5} {'Blocked':>8} {'Captcha':>8} {'Timeout':>8} {'Error':>6}")
    for tier, counts in matrix.items():
        print(
            f"{tier:<18} {counts['ok']:>5} {counts['blocked']:>8} "
            f"{counts['captcha']:>8} {counts['timeout']:>8} {counts['error']:>6}"
        )

    out_path = Path("data") / f"tier_benchmark_{date.today().isoformat().replace('-', '')}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"matrix": matrix, "results": results, "elapsed_s": elapsed}, indent=2)
    )
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
