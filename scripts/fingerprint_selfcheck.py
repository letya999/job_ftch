"""Live fingerprint self-check: runnable proof of identity coherence (TRACK A / A6).

Launches each installed browser tier against a LOCAL page (no third-party
detector), reads every I1-I9 axis in the window, classic Worker, and module
Worker realms, and runs the same :func:`cross_check_observed` contract the tests
use. Prints a per-tier PASS/FAIL table. This is a developer tool, not a CI test
- it needs real browsers installed.

Usage:
    uv run python -m scripts.fingerprint_selfcheck [tier ...]

With no args it probes the default browser tiers that are installed.
"""

from __future__ import annotations

import asyncio
import re
import sys
import threading
import traceback
from contextlib import suppress
from dataclasses import replace
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import structlog

from job_ftch.infrastructure.bypass.context import BypassContext
from job_ftch.infrastructure.bypass.identity.coherence import cross_check_observed
from job_ftch.infrastructure.bypass.identity.model import SessionIdentity

logger = structlog.get_logger("job_ftch.scripts.fingerprint_selfcheck")

_FIXTURE = Path("tests/fixtures/fpcheck/index.html").resolve()
_DEFAULT_TIERS = ("patchright_browser", "nodriver", "camoufox", "cloak")
_CHROME_VERSION_RE = re.compile(r"Chrome/(\d+)")
_FIREFOX_VERSION_RE = re.compile(r"Firefox/(\d+)")


class _QuietFixtureHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


class _QuietFixtureServer(ThreadingHTTPServer):
    def handle_error(self, request: object, client_address: object) -> None:
        return


def _start_fixture_server() -> tuple[ThreadingHTTPServer, str]:
    handler = partial(_QuietFixtureHandler, directory=str(_FIXTURE.parent))
    server = _QuietFixtureServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}/{_FIXTURE.name}"


def _identity_for_observed_firefox(
    base_persona: object, window: dict[str, object]
) -> SessionIdentity:
    """Build the declared identity for Camoufox's native BrowserForge output."""
    ua = str(window.get("userAgent", ""))
    version_match = _FIREFOX_VERSION_RE.search(ua)
    version = version_match.group(1) if version_match else ""
    persona = replace(
        base_persona,
        ua=ua,
        sec_ch_ua="",
        sec_ch_ua_platform="",
        navigator_platform=str(window.get("platform", "")),
        locale=str(window.get("language", "")),
        timezone=str(window.get("timezone", "")),
        webgl_renderer=str(window.get("webglRenderer", "")),
        browser_family="firefox",
        browser_version=version or getattr(base_persona, "browser_version", ""),
    )
    return SessionIdentity.for_persona(persona, derived_from="runtime")


def _identity_for_observed_chromium(
    base_persona: object, window: dict[str, object]
) -> SessionIdentity:
    """Project native Chromium-family runtime surfaces into the declared story."""
    ua = str(window.get("userAgent", ""))
    version_match = _CHROME_VERSION_RE.search(ua)
    version = version_match.group(1) if version_match else ""
    persona = replace(
        base_persona,
        ua=ua,
        navigator_platform=str(window.get("platform", "")),
        locale=str(window.get("language", "")),
        timezone=str(window.get("timezone", "")),
        webgl_renderer=str(window.get("webglRenderer", "")),
        browser_family="chromium",
        browser_version=version or getattr(base_persona, "browser_version", ""),
    )
    return SessionIdentity.for_persona(persona, derived_from="runtime")


async def _safe_evaluate(page: object, script: str) -> object:
    evaluate = getattr(page, "evaluate", None)
    if not callable(evaluate):
        return None
    return await evaluate(script)


async def _safe_goto(page: object, url: str) -> None:
    from job_ftch.infrastructure.sources.browser_utils import navigate

    await navigate(
        page,  # type: ignore[arg-type]
        url,
        {
            "_allow_private_selfcheck_fixture": True,
            "wait_until": "domcontentloaded",
            "challenge_retries": 0,
        },
    )


async def _wait_for_fpcheck(page: object, *, timeout_seconds: float = 8.0) -> object:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last: object = None
    while asyncio.get_running_loop().time() < deadline:
        last = await _safe_evaluate(page, "window.__fpcheck || null")
        if isinstance(last, dict) and "window" in last:
            return last
        last = await _safe_evaluate(
            page,
            """
            (() => {
              const out = document.getElementById('out');
              if (!out || !out.textContent || out.textContent === 'collecting…') return null;
              try { return JSON.parse(out.textContent); } catch (_) { return null; }
            })()
            """,
        )
        if isinstance(last, dict) and "window" in last:
            return last
        await asyncio.sleep(0.1)
    return last


async def _probe_tier(tier: str, url: str) -> tuple[str, bool, list[str]]:
    from job_ftch.application.registry import resolve_bypass
    from job_ftch.infrastructure.sources.browser_utils import open_page

    strategy = resolve_bypass("auto", {})
    try:
        ctx = await BypassContext.for_url(url, config={})
        bind = getattr(strategy, "bind_context", None)
        if callable(bind):
            bind(ctx)
        escalate_to = getattr(strategy, "escalate_to", None)
        if callable(escalate_to):
            try:
                escalate_to(tier)
            except Exception:
                return (tier, False, ["escalate_to_failed"])
        persona = ctx.persona
    except Exception as exc:
        return (tier, False, [f"context_failed:{type(exc).__name__}"])

    identity = SessionIdentity.for_persona(persona)
    config = {
        "url": url,
        "persistent_context": False,
        "_allow_private_selfcheck_fixture": True,
    }
    prepare = getattr(strategy, "prepare_browser_config", None)
    if callable(prepare):
        config = prepare(config)
    href: object = ""
    ready: object = ""
    try:
        async with open_page(config, bypass_strategy=strategy) as page:
            await _safe_goto(page, url)
            fp = await _wait_for_fpcheck(page)
            identity = SessionIdentity.for_persona(ctx.persona)
            if not isinstance(fp, dict) or "window" not in fp:
                href = await _safe_evaluate(page, "location.href")
                ready = await _safe_evaluate(page, "document.readyState")
    except Exception as exc:
        tb = " | ".join(traceback.format_exception(exc)[-4:]).replace("\n", " ")
        return (tier, False, [f"open_failed:{type(exc).__name__}:{exc!s}:{tb}"])

    if not isinstance(fp, dict) or "window" not in fp:
        return (tier, False, [f"no_fpcheck:href={href!r}:ready={ready!r}"])
    window = fp.get("window") or {}
    worker = fp.get("worker") or {}
    module_worker = fp.get("moduleWorker") or {}
    if tier == "camoufox":
        identity = _identity_for_observed_firefox(ctx.persona, window)
    elif tier in {"nodriver", "cloak"}:
        identity = _identity_for_observed_chromium(ctx.persona, window)
    report = cross_check_observed(identity, window=window, worker=worker)
    issues = [f"{i.code}:{i.axis}:{i.detail}" for i in report.issues]
    if module_worker:
        module_report = cross_check_observed(identity, window=window, worker=module_worker)
        issues.extend(f"module_worker:{i.code}:{i.axis}:{i.detail}" for i in module_report.issues)
    else:
        issues.append("module_worker_missing")
    return (
        tier,
        not issues,
        issues,
    )


async def main(argv: list[str]) -> int:
    if not _FIXTURE.exists():
        print(f"fixture not found: {_FIXTURE}")
        return 1
    server, url = _start_fixture_server()
    try:
        tiers = argv or list(_DEFAULT_TIERS)
        print(f"self-check against {url}\n")
        all_ok = True
        for tier in tiers:
            name, ok, issues = await _probe_tier(tier, url)
            status = "PASS" if ok else "FAIL"
            detail = "" if ok else "  " + ", ".join(issues)
            print(f"  [{status}] {name}{detail}")
            all_ok = all_ok and ok
    finally:
        server.shutdown()
        with suppress(Exception):
            server.server_close()
    print("\n" + ("all tiers coherent" if all_ok else "coherence issues found"))
    return 0 if all_ok else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
