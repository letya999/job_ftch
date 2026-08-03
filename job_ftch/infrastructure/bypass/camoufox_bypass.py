from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import asynccontextmanager, suppress
from typing import Any

from job_ftch.application.registry import BypassCapability, register_bypass
from job_ftch.config import get_settings

try:
    from camoufox.async_api import AsyncCamoufox as _ImportedAsyncCamoufox

    AsyncCamoufox: Any = _ImportedAsyncCamoufox
    _CAMOUFOX_AVAILABLE = True
except ImportError:
    AsyncCamoufox = None
    _CAMOUFOX_AVAILABLE = False


class CamoufoxBypass:
    """Browser-tier bypass powered by Camoufox's async Playwright-like wrapper."""

    def __init__(
        self,
        *,
        os_name: str | None = None,
    ) -> None:
        self._os_name = os_name

    async def apply_http(self, client: Any) -> Any:
        return client

    def apply_browser_args(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return kwargs

    async def apply_page(self, page: Any) -> None:
        del page
        return None

    @asynccontextmanager
    async def open_page(self, config: dict[str, Any], *, use_proxy: bool = False) -> Any:
        if AsyncCamoufox is None:
            raise ImportError(
                "Camoufox bypass requires the 'browser' extra with camoufox installed."
            )

        browser_kwargs: dict[str, Any] = {"headless": config.get("headless", True)}
        if self._os_name:
            browser_kwargs["os"] = self._os_name
        # Camoufox owns its own fingerprint at the C++ level via BrowserForge:
        # we pass high-level constraints and let it fill a coherent Firefox
        # identity, instead of injecting our Chromium persona (defect A9).
        browser_kwargs["humanize"] = True
        proxy_active = bool(
            use_proxy and (config.get("_proxy_url") or os.environ.get("JOB_FTCH_HTTP_PROXY"))
        )
        if proxy_active:
            # Native geo coherence: Camoufox derives locale/timezone/geolocation
            # from the proxy exit IP (defect A7), and WebRTC is blocked so the
            # real IP cannot leak past the proxy.
            browser_kwargs["geoip"] = True
            browser_kwargs["block_webrtc"] = True
        elif config.get("locale"):
            # Without geoip, honour an explicitly configured locale; with geoip
            # the exit IP is the single source of truth, so we do not override it.
            browser_kwargs["locale"] = config["locale"]
        if config.get("disable_http2"):
            browser_kwargs["args"] = ["--disable-http2"]

        viewport = config.get("viewport")
        if isinstance(viewport, dict):
            width = int(viewport.get("width", 1440) or 1440)
            height = int(viewport.get("height", 900) or 900)
            browser_kwargs["window"] = (width, height)

        user_data_dir: str | None = None
        owns_profile_dir = False
        if config.get("persistent_context"):
            shared_profile_dir = config.get("_profile_dir")
            user_data_dir = (
                str(shared_profile_dir)
                if shared_profile_dir
                else tempfile.mkdtemp(prefix="camoufox_profile_")
            )
            owns_profile_dir = not bool(shared_profile_dir)
            browser_kwargs["persistent_context"] = True
            browser_kwargs["user_data_dir"] = user_data_dir

        if use_proxy:
            proxy_url = config.get("_proxy_url") or os.environ.get("JOB_FTCH_HTTP_PROXY")
            if proxy_url:
                browser_kwargs["proxy"] = {"server": proxy_url}

        try:
            created_context: Any = None
            async with AsyncCamoufox(**browser_kwargs) as browser:
                context = browser
                if not config.get("persistent_context") and hasattr(browser, "new_context"):
                    context_kwargs: dict[str, Any] = {}
                    # Deliberately NOT setting user_agent: forcing our Chromium
                    # persona's UA onto a Firefox engine is an instant cross-check
                    # failure (A9). Camoufox's generated UA is authoritative.
                    if isinstance(viewport, dict):
                        context_kwargs["viewport"] = viewport
                    # Skip locale override when geoip owns it (proxy active).
                    if config.get("locale") and not proxy_active:
                        context_kwargs["locale"] = config["locale"]
                    if config.get("skip_ssl"):
                        context_kwargs["ignore_https_errors"] = True
                    created_context = await browser.new_context(**context_kwargs)
                    context = created_context

                if hasattr(context, "set_default_timeout"):
                    context.set_default_timeout(
                        config.get("timeout", get_settings().browser_default_timeout_ms)
                    )
                if config.get("cookies") and hasattr(context, "add_cookies"):
                    await context.add_cookies(config["cookies"])

                page = await context.new_page()
                try:
                    if config.get("warmup_url"):
                        await page.goto(config["warmup_url"])
                    yield page
                finally:
                    with suppress(Exception):
                        await page.close()
                    if created_context is not None and hasattr(created_context, "close"):
                        with suppress(Exception):
                            await created_context.close()
        finally:
            if user_data_dir is not None and owns_profile_dir:
                shutil.rmtree(user_data_dir, ignore_errors=True)


def _create_camoufox(bypass_config: dict[str, Any] | None = None) -> CamoufoxBypass:
    config = bypass_config or {}
    return CamoufoxBypass(
        os_name=config.get("os"),
    )


if _CAMOUFOX_AVAILABLE:
    register_bypass(
        "camoufox",
        capability=BypassCapability(
            cost=40,
            browser_family="firefox",
            owns_session=True,
            challenge_actions=frozenset({"fingerprint_resistant", "generic_challenge"}),
            min_remaining_seconds=12.0,
        ),
    )(_create_camoufox)
