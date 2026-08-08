"""Run one background-priming cycle over configured source domains (TRACK C).

Opt-in: does nothing unless ``JOB_FTCH_BYPASS_BACKGROUND_PRIMING_ENABLED=true``.
Visits each cold / near-expiry domain root at the DomainPacer rate, lets the free
browser_wait tier auto-clear the challenge, and leaves the clearance cookie in the
per-domain persistent profile so the live crawl hits a warm session.

Usage:
    uv run python -m scripts.prime_domains [domain-or-url ...]

With no args it reads the CIS career-site fixture and primes those domains.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import structlog
import yaml

from job_ftch.config import get_settings
from job_ftch.infrastructure.bypass.priming import BackgroundPrimer, _normalize_domain

logger = structlog.get_logger("job_ftch.scripts.prime_domains")

_FIXTURE = Path("fixtures/sources/career_sites_cis_303.yaml")


def _load_fixture_domains() -> list[str]:
    if not _FIXTURE.exists():
        logger.error("fixture_not_found", path=str(_FIXTURE))
        return []
    data = yaml.safe_load(_FIXTURE.read_text(encoding="utf-8"))
    domains: list[str] = []
    if isinstance(data, list):
        for item in data:
            raw = (
                item
                if isinstance(item, str)
                else (item.get("url") if isinstance(item, dict) else "")
            )
            domain = _normalize_domain(str(raw or ""))
            if domain:
                domains.append(domain)
    return domains


async def main(argv: list[str]) -> int:
    settings = get_settings()
    if not getattr(settings, "bypass_background_priming_enabled", False):
        print(
            "Background priming is disabled. Set "
            "JOB_FTCH_BYPASS_BACKGROUND_PRIMING_ENABLED=true to enable."
        )
        return 0
    domains = [_normalize_domain(a) for a in argv] if argv else _load_fixture_domains()
    domains = [d for d in domains if d]
    if not domains:
        print("No domains to prime.")
        return 1
    primer = BackgroundPrimer(settings)
    report = await primer.prime_domains(domains)
    print(
        "priming cycle: "
        f"primed={report.primed} refreshed={report.refreshed} "
        f"skipped_warm={report.skipped_warm} failed={report.failed} "
        f"budget_exhausted={report.budget_exhausted} "
        f"hit={report.primed_hit} miss={report.primed_miss}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
