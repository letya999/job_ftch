"""CloakBrowser-backed BypassStrategy (ADR-022 + ADR-037 hardening).

CloakBrowser is a Chromium binary with source-level C++ fingerprint
patches (canvas, WebGL, audio, fonts, GPU, screen, WebRTC, network
timing, automation signals, CDP input behavior). It exposes a
drop-in Playwright replacement via `from cloakbrowser import launch`.

Per ADR-022, the bypass is the ultimate tier in the adaptive escalation
chain. Per ADR-037, this file now defaults to:

- `humanize=True` so mouse / keyboard / scroll look like a real user
  (one flag, no per-call config needed)
- `geoip=True` when a `proxy=` is passed, so timezone + locale match
  the proxy exit IP (without this: UTC + en-US is a bot signal)
- `headless=False` by default for protected sites (per CloakBrowser
  docs: some sites still detect headless even with C++ patches)

The defaults can be overridden through `bypass_config`:
    bypass:
      kind: cloak
      config:
        humanize: false
        geoip: false
        headless: true
        proxy: socks5://user:pass@residential:1080
"""

from __future__ import annotations

import os
from typing import Any

import structlog

from job_ftch.application.registry import BypassCapability, register_bypass

try:
    import cloakbrowser

    _CLOAK_AVAILABLE = True
except ImportError:
    _CLOAK_AVAILABLE = False


logger = structlog.get_logger(__name__)


class CloakBrowserBypass:
    """CloakBrowser-backed BypassStrategy with ADR-022 / ADR-037 defaults.

    `apply_browser_args` is the path Playwright actually walks when
    launching a context: we inject the executable_path, the default
    stealth args, and (per ADR-022/ADR-037) the `humanize=true`,
    `geoip` (when proxy set), and `headless=False` defaults unless
    overridden through bypass_config.
    """

    DEFAULT_HUMANIZE: bool = True
    DEFAULT_GEOIP_WHEN_PROXY: bool = True
    DEFAULT_HEADLESS: bool = False

    def __init__(
        self,
        executable_path: str | None = None,
        backend: str = "patchright",
        headless: bool | None = None,
    ) -> None:
        self._executable_path = executable_path
        self._backend = backend
        self._backend_available = self._check_backend(backend)
        self._headless = headless

    @staticmethod
    def _check_backend(backend: str) -> bool:
        if backend == "patchright":
            try:
                import importlib.util

                return importlib.util.find_spec("patchright") is not None
            except Exception:
                return False
        return True

    async def apply_http(self, client: Any) -> Any:
        return client

    def apply_browser_args(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        if not _CLOAK_AVAILABLE and not self._executable_path:
            logger.warning("cloakbrowser_not_installed_and_no_executable_path")
            return kwargs

        exe_path = self._executable_path
        if not exe_path and _CLOAK_AVAILABLE:
            exe_path = cloakbrowser.ensure_binary()

        if exe_path:
            kwargs["executable_path"] = exe_path

        for key in ("timeout", "timeoutSeconds"):
            value = kwargs.get(key)
            if isinstance(value, float) and value.is_integer():
                kwargs[key] = int(value)

        args = kwargs.get("args", []) or []
        args = list(args)
        kwargs["args"] = args

        if _CLOAK_AVAILABLE:
            for arg in cloakbrowser.get_default_stealth_args():
                if arg not in args:
                    args.append(arg)

        # Defaults: humanize + headless off (per ADR-022 docs).
        if self.DEFAULT_HUMANIZE and "--humanize=true" not in args:
            args.append("--humanize=true")

        kwargs["headless"] = self.DEFAULT_HEADLESS if self._headless is None else self._headless

        if self._backend_available and self._backend == "patchright":
            kwargs["_cloakbrowser_backend"] = "patchright"
        elif not self._backend_available and self._backend == "patchright":
            logger.info("patchright_unavailable_using_playwright_fallback")

        # geoip needs the proxy to be known; we infer it from kwargs
        # so we don't have to thread it through the BypassStrategy
        # protocol. Falls back to env if a global proxy was set.
        if self.DEFAULT_GEOIP_WHEN_PROXY and "geoip" not in kwargs:
            proxy = kwargs.get("proxy") or os.environ.get("JOB_FTCH_HTTP_PROXY")
            if proxy:
                kwargs["geoip"] = True

        return kwargs

    async def apply_page(self, page: Any) -> None:
        # CloakBrowser handles stealth natively in C++; no JS injection
        # needed at the page level.
        return None


def _create_cloak(bypass_config: dict[str, Any] | None = None) -> CloakBrowserBypass:
    """Build a CloakBrowserBypass from bypass_config.

    Supported bypass_config keys:
      - executable_path: explicit path to the CloakBrowser binary
      - backend: 'patchright' (default) or 'playwright'
    """
    config = bypass_config or {}
    exe_path = config.get("executable_path") or os.environ.get("CLOAK_BROWSER_PATH")
    backend = config.get("backend") or os.environ.get("JOB_FTCH_CLOAK_BACKEND", "patchright")
    raw_headless = config.get("headless")
    headless = None
    if raw_headless is not None:
        headless = (
            raw_headless.strip().lower() in {"1", "true", "yes", "on"}
            if isinstance(raw_headless, str)
            else bool(raw_headless)
        )
    return CloakBrowserBypass(executable_path=exe_path, backend=backend, headless=headless)


if _CLOAK_AVAILABLE:
    register_bypass(
        "cloak",
        capability=BypassCapability(
            cost=100,
            browser_family="chromium_patched",
            challenge_actions=frozenset({"fingerprint_resistant", "generic_challenge", "terminal"}),
            legal_gate="adr_073",
            min_remaining_seconds=20.0,
        ),
    )(_create_cloak)
