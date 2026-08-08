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


class CamoufoxAdapter:
    name = "camoufox"
    family = "camoufox-firefox"
    default_expected_failure = False

    async def run(self, config: ClientRunConfig) -> ClientRunResult:
        if importlib.util.find_spec("camoufox") is None:
            return ClientRunResult.skipped_result(
                self.name, self.family, "optional camoufox package/browser is not installed"
            )
        from camoufox.async_api import AsyncCamoufox

        sid = new_session_id(self.name)
        target = build_target_url(
            config, session_id=sid, client_name=self.name, client_family=self.family
        )
        try:
            async with AsyncCamoufox(headless=config.headless) as browser:
                context = (
                    await browser.new_context(ignore_https_errors=True)
                    if hasattr(browser, "new_context")
                    else browser
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
                if context is not browser and hasattr(context, "close"):
                    await context.close()
        except Exception as exc:
            return ClientRunResult.skipped_result(
                self.name, self.family, f"camoufox launch/run failed: {type(exc).__name__}: {exc}"
            )
        return result_from_finish(
            name=self.name, family=self.family, session_id=sid, payload=payload
        )
