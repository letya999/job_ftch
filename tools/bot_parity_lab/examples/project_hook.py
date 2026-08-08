"""Owned-browser integration example.

Set:
    PARITYLAB_CLIENT_HOOK=examples.project_hook:run_owned_browser

Replace `launch_owned_browser` with the browser factory used by your scraper. The
hook receives only a localhost URL and must not be pointed at third-party sites.
"""

from __future__ import annotations

from typing import Any

from paritylab.clients.base import ClientHookContext


async def launch_owned_browser(url: str) -> dict[str, Any]:
    """Replace this body with the browser client from the audited project."""
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(ignore_https_errors=True)
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_function("window.__parityLabReady === true")
        # Deliberately deterministic interaction: the lab should report what it sees.
        await page.locator("#interaction-target").click()
        await page.locator("#interaction-input").fill("owned-client")
        await page.evaluate("window.scrollTo(0, 460)")
        await page.wait_for_timeout(250)
        await page.evaluate("window.parityLabFinish()")
        await page.wait_for_function("window.__parityLabDone === true")
        result = await page.evaluate("window.__parityLabResult")
        await context.close()
        await browser.close()
        return {"pageResult": result, "implementation": "replace-with-owned-browser"}


async def run_owned_browser(context: ClientHookContext) -> dict[str, Any]:
    context.artifacts_dir.mkdir(parents=True, exist_ok=True)
    return await launch_owned_browser(context.url)
