from __future__ import annotations

import importlib.util

from paritylab.clients.base import (
    ClientRunConfig,
    ClientRunResult,
    build_target_url,
    new_session_id,
    result_from_finish,
)
from paritylab.clients.playwright_client import exercise_playwright_page


class PatchrightAdapter:
    name = "patchright"
    family = "patchright-chromium"
    default_expected_failure = False

    async def run(self, config: ClientRunConfig) -> ClientRunResult:
        if importlib.util.find_spec("patchright") is None:
            return ClientRunResult.skipped_result(
                self.name, self.family, "optional patchright package is not installed"
            )
        from patchright.async_api import async_playwright

        sid = new_session_id(self.name)
        target = build_target_url(
            config, session_id=sid, client_name=self.name, client_family=self.family
        )
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=config.headless)
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
                self.name, self.family, f"patchright launch/run failed: {type(exc).__name__}: {exc}"
            )
        return result_from_finish(
            name=self.name, family=self.family, session_id=sid, payload=payload
        )
