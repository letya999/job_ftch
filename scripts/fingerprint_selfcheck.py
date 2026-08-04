"""Live fingerprint self-check: runnable proof of identity coherence (TRACK A / A6).

Launches each installed browser tier against a LOCAL page (no third-party
detector), reads every I1-I9 axis in the window AND worker realms, and runs the
same :func:`cross_check_observed` contract the tests use. Prints a per-tier
PASS/FAIL table. This is a developer tool, not a CI test - it needs real
browsers installed.

Usage:
    uv run python -m scripts.fingerprint_selfcheck [tier ...]

With no args it probes the default browser tiers that are installed.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import structlog

from job_ftch.infrastructure.bypass.context import BypassContext
from job_ftch.infrastructure.bypass.identity.coherence import cross_check_observed
from job_ftch.infrastructure.bypass.identity.model import SessionIdentity

logger = structlog.get_logger("job_ftch.scripts.fingerprint_selfcheck")

_FIXTURE = Path("tests/fixtures/fpcheck/index.html").resolve()
_DEFAULT_TIERS = ("patchright_browser", "nodriver", "camoufox", "cloak")


async def _probe_tier(tier: str, url: str) -> tuple[str, bool, list[str]]:
    from job_ftch.application.registry import resolve_bypass
    from job_ftch.infrastructure.sources.browser_utils import open_page

    strategy = resolve_bypass("auto", {})
    escalate_to = getattr(strategy, "escalate_to", None)
    if callable(escalate_to):
        try:
            escalate_to(tier)
        except Exception:
            return (tier, False, ["escalate_to_failed"])
    try:
        ctx = await BypassContext.for_url(url, config={})
        bind = getattr(strategy, "bind_context", None)
        if callable(bind):
            bind(ctx)
        persona = ctx.persona
    except Exception as exc:
        return (tier, False, [f"context_failed:{type(exc).__name__}"])

    identity = SessionIdentity.for_persona(persona)
    config = {"url": url, "warmup_url": url, "persistent_context": True}
    prepare = getattr(strategy, "prepare_browser_config", None)
    if callable(prepare):
        config = prepare(config)
    try:
        async with open_page(config, bypass_strategy=strategy) as page:
            await asyncio.sleep(2.0)
            fp = await page.evaluate("window.__fpcheck")
    except Exception as exc:
        return (tier, False, [f"open_failed:{type(exc).__name__}"])

    if not isinstance(fp, dict) or "window" not in fp:
        return (tier, False, ["no_fpcheck"])
    window = fp.get("window") or {}
    worker = fp.get("worker") or {}
    report = cross_check_observed(identity, window=window, worker=worker)
    return (tier, report.ok, [f"{i.code}:{i.axis}" for i in report.issues])


async def main(argv: list[str]) -> int:
    if not _FIXTURE.exists():
        print(f"fixture not found: {_FIXTURE}")
        return 1
    url = _FIXTURE.as_uri()
    tiers = argv or list(_DEFAULT_TIERS)
    print(f"self-check against {url}\n")
    all_ok = True
    for tier in tiers:
        name, ok, issues = await _probe_tier(tier, url)
        status = "PASS" if ok else "FAIL"
        detail = "" if ok else "  " + ", ".join(issues)
        print(f"  [{status}] {name}{detail}")
        all_ok = all_ok and ok
    print("\n" + ("all tiers coherent" if all_ok else "coherence issues found"))
    return 0 if all_ok else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
