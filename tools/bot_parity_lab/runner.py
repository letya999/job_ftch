from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx

from tools.bot_parity_lab.scoring import ScoreReport, score_snapshot, to_markdown
from tools.bot_parity_lab.server import LabServer

DEFAULT_CLIENTS = (
    "httpx_raw",
    "patchright_plain",
    "patchright_browser",
    "nodriver",
    "camoufox",
    "cloak",
)


async def run_httpx_raw(url: str) -> None:
    async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
        response = await client.get(url)
        response.raise_for_status()


async def exercise_page(page: Any) -> None:
    mouse = getattr(page, "mouse", None)
    if mouse is not None:
        move = getattr(mouse, "move", None)
        wheel = getattr(mouse, "wheel", None)
        if callable(move):
            await move(90, 90)
            await move(160, 120)
        if callable(wheel):
            await wheel(0, 280)
    evaluate = getattr(page, "evaluate", None)
    if callable(evaluate):
        await evaluate(
            """
            (() => {
              window.dispatchEvent(new PointerEvent('pointermove', {clientX: 120, clientY: 96}));
              window.dispatchEvent(new MouseEvent('mousemove', {clientX: 120, clientY: 96}));
              window.scrollTo({top: 220, behavior: 'instant'});
              window.dispatchEvent(new Event('scroll'));
            })()
            """
        )
    click = getattr(page, "click", None)
    if callable(click):
        await click("#go")
    else:
        if callable(evaluate):
            await evaluate("document.querySelector('#go')?.click()")
    keyboard = getattr(page, "keyboard", None)
    if keyboard is not None:
        press = getattr(keyboard, "press", None)
        if callable(press):
            await press("Tab")


async def run_patchright_plain(url: str) -> None:
    from patchright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--headless=new"])
        context = await browser.new_context(locale="en-US", timezone_id="America/New_York")
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        await exercise_page(page)
        await page.wait_for_timeout(1200)
        await browser.close()


async def run_job_ftch_tier(url: str, tier: str) -> None:
    from job_ftch.application.registry import resolve_bypass
    from job_ftch.infrastructure.bypass.context import BypassContext
    from job_ftch.infrastructure.sources.browser_utils import navigate, open_page

    strategy = resolve_bypass("auto", {})
    ctx = await BypassContext.for_url(url, config={})
    bind = getattr(strategy, "bind_context", None)
    if callable(bind):
        bind(ctx)
    escalate_to = getattr(strategy, "escalate_to", None)
    if callable(escalate_to):
        escalate_to(tier)
    config: dict[str, Any] = {
        "url": url,
        "persistent_context": False,
        "_allow_private_selfcheck_fixture": True,
        "challenge_retries": 0,
    }
    prepare = getattr(strategy, "prepare_browser_config", None)
    if callable(prepare):
        config = prepare(config)
    async with open_page(config, bypass_strategy=strategy) as page:
        await navigate(
            page,
            url,
            {
                "_allow_private_selfcheck_fixture": True,
                "wait_until": "domcontentloaded",
                "challenge_retries": 0,
            },
        )
        await exercise_page(page)
        wait_for_timeout = getattr(page, "wait_for_timeout", None)
        if callable(wait_for_timeout):
            await wait_for_timeout(3000)
        else:
            await asyncio.sleep(3.0)


async def run_client(url: str, client: str) -> tuple[dict[str, Any], str | None]:
    started = time.time()
    error: str | None = None
    try:
        if client == "httpx_raw":
            await run_httpx_raw(url)
        elif client == "patchright_plain":
            await run_patchright_plain(url)
        else:
            await run_job_ftch_tier(url, client)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    return {"client": client, "elapsed_seconds": round(time.time() - started, 3)}, error


async def main_async(args: argparse.Namespace) -> int:
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    clients = tuple(args.clients.split(",")) if args.clients else DEFAULT_CLIENTS
    allowed_failures = {
        client.strip() for client in str(args.allow_fail_clients or "").split(",") if client.strip()
    }
    reports: list[ScoreReport] = []
    raw: dict[str, Any] = {"runs": []}

    for client in clients:
        with LabServer() as server:
            meta, error = await run_client(server.url, client)
            await asyncio.sleep(0.2)
            snapshot = server.collector.snapshot()
        if error:
            snapshot["runner_error"] = error
        report = score_snapshot(client, snapshot)
        if error:
            from tools.bot_parity_lab.scoring import Finding

            report.findings.append(Finding("RUNNER_ERROR", "high", error))
        reports.append(report)
        raw["runs"].append({"meta": meta, "snapshot": snapshot, "report": report})
        print(f"[{'PASS' if report.ok else 'FAIL'}] {client} score={report.score}")
        for finding in report.findings:
            print(f"  - {finding.severity.upper()} {finding.code}: {finding.detail}")

    serializable = {
        "runs": [
            {
                "meta": run["meta"],
                "snapshot": run["snapshot"],
                "report": {
                    "client": run["report"].client,
                    "score": run["report"].score,
                    "ok": run["report"].ok,
                    "request_count": run["report"].request_count,
                    "event_count": run["report"].event_count,
                    "signal_count": run["report"].signal_count,
                    "findings": [asdict(finding) for finding in run["report"].findings],
                },
            }
            for run in raw["runs"]
        ]
    }
    (out_dir / "bot_parity_raw.json").write_text(
        json.dumps(serializable, indent=2, sort_keys=True), encoding="utf-8"
    )
    (out_dir / "bot_parity_report.md").write_text(to_markdown(reports), encoding="utf-8")
    return 0 if all(report.ok or report.client in allowed_failures for report in reports) else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local bot parity playground.")
    parser.add_argument(
        "--clients", help=f"comma-separated clients; default: {','.join(DEFAULT_CLIENTS)}"
    )
    parser.add_argument("--out", default="artifacts/bot_parity_lab")
    parser.add_argument(
        "--allow-fail-clients",
        default="httpx_raw,patchright_plain",
        help="comma-separated negative controls allowed to fail without a non-zero exit",
    )
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
