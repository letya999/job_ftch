from __future__ import annotations

import asyncio
import importlib.util
import os
import random
import shutil
from pathlib import Path
from typing import Any

from paritylab.clients.base import (
    ClientRunConfig,
    ClientRunResult,
    build_target_url,
    new_session_id,
    result_from_finish,
)


async def exercise_playwright_page(
    page: Any, *, session_id: str, screenshot_path: Path | None = None
) -> dict[str, Any]:
    try:
        await page.locator("#finish-button:not([disabled])").wait_for(
            state="visible", timeout=35_000
        )
    except Exception as exc:
        diagnostic = await page.locator("#status-message").text_content()
        bootstrap_status = await page.locator("#status-bootstrap").text_content()
        if screenshot_path is not None:
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(
                path=str(screenshot_path.with_name("bootstrap-failure.png")), full_page=True
            )
        raise RuntimeError(
            f"page bootstrap did not become ready: status={bootstrap_status!r}, "
            f"message={diagnostic!r}"
        ) from exc
    rng = random.Random(session_id)
    points = [
        (rng.randint(18, 42), rng.randint(18, 46)),
        (rng.randint(135, 215), rng.randint(70, 135)),
        (rng.randint(310, 430), rng.randint(190, 285)),
    ]
    for index, (x, y) in enumerate(points):
        await page.mouse.move(x, y, steps=rng.randint(5, 13) if index else 1)
        await page.wait_for_timeout(rng.randint(18, 75))
    await page.locator("#interaction-target").click()
    await page.locator("#interaction-input").click()
    await page.keyboard.type("parity", delay=rng.randint(35, 95))
    scroll_y = rng.randint(390, 540)
    await page.mouse.wheel(0, scroll_y)
    await page.wait_for_timeout(rng.randint(130, 310))
    await page.mouse.wheel(0, -scroll_y)
    await page.wait_for_timeout(rng.randint(170, 360))
    if screenshot_path is not None:
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(screenshot_path), full_page=True)
    finish_button = page.locator("#finish-button")
    if await finish_button.count() and await finish_button.is_enabled():
        await finish_button.click()
    else:
        await page.evaluate("window.parityLabFinish()")
    report_status = page.locator("#status-report")
    deadline = asyncio.get_running_loop().time() + 20
    while asyncio.get_running_loop().time() < deadline:
        status_text = (await report_status.text_content() or "").strip().lower()
        if status_text not in {"", "not finalized", "running", "finalizing"}:
            break
        await page.wait_for_timeout(100)
    else:
        raise RuntimeError("page report did not finalize within 20 seconds")
    if status_text == "failed":
        diagnostic = await page.locator("#status-message").text_content()
        if screenshot_path is not None:
            await page.screenshot(
                path=str(screenshot_path.with_name("finalization-failure.png")),
                full_page=True,
            )
        raise RuntimeError(f"page report finalization failed: {diagnostic}")
    result = await page.evaluate("window.__parityLabResult")
    if isinstance(result, dict) and isinstance(result.get("summary"), dict):
        return result

    report = await page.evaluate(
        """async sid => {
          const response = await fetch(`/api/report/${encodeURIComponent(sid)}?sid=${encodeURIComponent(sid)}`);
          if (!response.ok) throw new Error(`report API returned HTTP ${response.status}`);
          return response.json();
        }""",
        session_id,
    )
    session = report.get("session") if isinstance(report, dict) else None
    if not isinstance(session, dict):
        raise RuntimeError("page finalized without a structured parity-lab report")
    return {
        "ok": True,
        "summary": session.get("summary", {}),
        "finding_codes": [
            item.get("code")
            for item in session.get("findings", [])
            if isinstance(item, dict) and isinstance(item.get("code"), str)
        ],
        "session": session,
    }


class PlaywrightAdapter:
    name = "plain-playwright"
    family = "playwright-chromium"
    default_expected_failure = True

    async def run(self, config: ClientRunConfig) -> ClientRunResult:
        if importlib.util.find_spec("playwright") is None:
            return ClientRunResult.skipped_result(
                self.name, self.family, "playwright package is not installed"
            )
        from playwright.async_api import async_playwright

        sid = new_session_id(self.name)
        target = build_target_url(
            config, session_id=sid, client_name=self.name, client_family=self.family
        )
        try:
            async with async_playwright() as playwright:
                executable = os.getenv("PARITYLAB_CHROMIUM_EXECUTABLE") or next(
                    (
                        path
                        for name in (
                            "chromium",
                            "chromium-browser",
                            "google-chrome",
                            "google-chrome-stable",
                        )
                        if (path := shutil.which(name))
                    ),
                    None,
                )
                launch_options: dict[str, Any] = {"headless": config.headless}
                if executable is not None:
                    launch_options["executable_path"] = executable
                browser = await playwright.chromium.launch(**launch_options)
                context = await browser.new_context(
                    ignore_https_errors=True, viewport={"width": 1280, "height": 800}
                )
                page = await context.new_page()
                await page.goto(
                    target,
                    wait_until="domcontentloaded",
                    timeout=int(config.timeout_seconds * 1000),
                )
                payload = await exercise_playwright_page(
                    page,
                    session_id=sid,
                    screenshot_path=config.artifacts_dir / sid / "page.png",
                )
                await context.close()
                await browser.close()
        except Exception as exc:
            return ClientRunResult.skipped_result(
                self.name, self.family, f"playwright launch/run failed: {type(exc).__name__}: {exc}"
            )
        return result_from_finish(
            name=self.name, family=self.family, session_id=sid, payload=payload
        )


class PlaywrightWebKitAdapter:
    name = "playwright-webkit"
    family = "playwright-webkit"
    default_expected_failure = False

    async def run(self, config: ClientRunConfig) -> ClientRunResult:
        if importlib.util.find_spec("playwright") is None:
            return ClientRunResult.skipped_result(
                self.name, self.family, "playwright package is not installed"
            )
        from playwright.async_api import async_playwright

        sid = new_session_id(self.name)
        target = build_target_url(
            config, session_id=sid, client_name=self.name, client_family=self.family
        )
        try:
            async with async_playwright() as playwright:
                browser = await playwright.webkit.launch(headless=config.headless)
                context = await browser.new_context(
                    ignore_https_errors=True, viewport={"width": 1280, "height": 800}
                )
                page = await context.new_page()
                await page.goto(
                    target,
                    wait_until="domcontentloaded",
                    timeout=int(config.timeout_seconds * 1000),
                )
                payload = await exercise_playwright_page(
                    page,
                    session_id=sid,
                    screenshot_path=config.artifacts_dir / sid / "page.png",
                )
                await context.close()
                await browser.close()
        except Exception as exc:
            return ClientRunResult.skipped_result(
                self.name,
                self.family,
                f"playwright WebKit launch/run failed: {type(exc).__name__}: {exc}",
            )
        return result_from_finish(
            name=self.name, family=self.family, session_id=sid, payload=payload
        )


class _PlaywrightBrowserTypeAdapter:
    browser_type = ""
    channel: str | None = None
    default_expected_failure = False

    async def run(self, config: ClientRunConfig) -> ClientRunResult:
        if importlib.util.find_spec("playwright") is None:
            return ClientRunResult.skipped_result(
                self.name, self.family, "playwright package is not installed"
            )
        from playwright.async_api import async_playwright

        sid = new_session_id(self.name)
        target = build_target_url(
            config, session_id=sid, client_name=self.name, client_family=self.family
        )
        try:
            async with async_playwright() as playwright:
                browser_type = getattr(playwright, self.browser_type)
                launch_options: dict[str, Any] = {"headless": config.headless}
                if self.channel is not None:
                    launch_options["channel"] = self.channel
                browser = await browser_type.launch(**launch_options)
                context = await browser.new_context(
                    ignore_https_errors=True, viewport={"width": 1280, "height": 800}
                )
                page = await context.new_page()
                await page.goto(
                    target,
                    wait_until="domcontentloaded",
                    timeout=int(config.timeout_seconds * 1000),
                )
                payload = await exercise_playwright_page(
                    page,
                    session_id=sid,
                    screenshot_path=config.artifacts_dir / sid / "page.png",
                )
                await context.close()
                await browser.close()
        except Exception as exc:
            return ClientRunResult.skipped_result(
                self.name,
                self.family,
                f"Playwright {self.browser_type} launch/run failed: {type(exc).__name__}: {exc}",
            )
        return result_from_finish(
            name=self.name, family=self.family, session_id=sid, payload=payload
        )


class PlaywrightChromeChannelAdapter(_PlaywrightBrowserTypeAdapter):
    name = "playwright-chrome-channel"
    family = "playwright-installed-chrome"
    browser_type = "chromium"
    channel = "chrome"


class PlaywrightFirefoxAdapter(_PlaywrightBrowserTypeAdapter):
    name = "playwright-firefox"
    family = "playwright-firefox"
    browser_type = "firefox"
