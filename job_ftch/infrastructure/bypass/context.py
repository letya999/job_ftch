"""Unified bypass facade: single entry point for all bypass modules.

BypassContext wires persona, fingerprint_profile, humanize, stealth_hardening,
risk_router, and domain_intel into one object that calling code (browser_utils,
career_site_source) consumes through a minimal interface.
"""

from __future__ import annotations

import contextlib
from typing import Any
from urllib.parse import urlparse

import structlog

from job_ftch.application.registry import resolve_bypass
from job_ftch.infrastructure.bypass.domain_intel import get_domain_intel
from job_ftch.infrastructure.bypass.humanize import get_humanizer
from job_ftch.infrastructure.bypass.pacing import get_domain_pacer
from job_ftch.infrastructure.bypass.persona import (
    BrowserPersona,
    align_persona_version,
    select_persona,
)
from job_ftch.infrastructure.bypass.preflight import PreflightResult, run_preflight
from job_ftch.infrastructure.bypass.risk_router import get_router
from job_ftch.infrastructure.bypass.stealth_hardening import apply_persona_hardening

logger = structlog.get_logger("job_ftch.bypass.context")


class BypassContext:
    """Facade over the bypass module graph.

    Usage:
        ctx = await BypassContext.for_url(url, config=cfg)
        headers = await ctx.before_request(url)
        # ... in browser path:
        await ctx.on_page(page)
        # after result:
        ctx.record_success(url)
    """

    __slots__ = (
        "persona",
        "preflight",
        "tier",
        "reason",
        "_domain",
        "_proxy",
        "_residential_proxy",
        "_network",
    )

    def __init__(
        self,
        *,
        persona: BrowserPersona,
        preflight: PreflightResult,
        domain: str,
        proxy: Any = None,
        residential_proxy: Any = None,
    ) -> None:
        self.persona = persona
        self.preflight = preflight
        self.tier = preflight.tier
        self.reason = preflight.reason
        self._domain = domain
        self._proxy = proxy
        self._residential_proxy = residential_proxy
        self._network = "direct"

    def set_effective_route(self, *, tier: str, network: str = "direct") -> None:
        """Update metrics state from the authoritative adaptive controller."""
        self.tier = tier
        self._network = network

    def set_browser_family(self, browser_family: str | None) -> None:
        """Align the persona when a route switches browser families."""
        if browser_family is None:
            return
        self.persona = select_persona(self._domain, browser_family)

    def align_browser_runtime(self, browser_family: str, reported_version: str) -> None:
        """Use the launched browser's version instead of a declared static version."""
        self.persona = align_persona_version(
            self.persona,
            browser_family,
            reported_version,
        )

    @property
    def domain(self) -> str:
        """Public accessor for the source domain (consumed by route/obfuscation layers)."""
        return self._domain

    @property
    def robots_blocked(self) -> bool:
        """Whether an enforced robots.txt policy disallowed this source (Wave 5.1)."""
        return bool(getattr(self.preflight, "robots_blocked", False))

    @property
    def proxy_available(self) -> bool:
        """Whether an actual proxy endpoint is configured for this source."""
        return bool(self.current_proxy_url)

    @property
    def residential_proxy_available(self) -> bool:
        """Whether a residential proxy endpoint is configured."""
        rp = self._residential_proxy
        return bool(getattr(rp, "available", False) or getattr(rp, "current_url", None))

    @property
    def current_proxy_url(self) -> str | None:
        active = self._active_proxy
        current_url_for_domain = getattr(active, "current_url_for_domain", None)
        if callable(current_url_for_domain):
            value = current_url_for_domain(self._domain)
            return str(value) if value else None
        value = getattr(active, "current_url", None)
        return str(value) if value else None

    @property
    def _active_proxy(self) -> Any:
        if self._network == "residential_proxy" and self._residential_proxy:
            return self._residential_proxy
        return self._proxy

    def rotate_proxy(
        self,
        url: str,
        *,
        status_code: int | None = None,
        body: bytes | None = None,
        error: BaseException | None = None,
    ) -> None:
        """Report a failed endpoint to a capable proxy provider and rotate it."""
        active = self._active_proxy
        handler = getattr(active, "handle_failure", None)
        if callable(handler):
            handler(
                url,
                status_code=status_code,
                body=body,
                error=error,
            )

    @classmethod
    async def for_url(
        cls,
        url: str,
        *,
        config: dict[str, Any] | None = None,
        browser_family: str | None = None,
    ) -> BypassContext:
        domain = urlparse(url).netloc.lower()
        # Default to the Chromium family (defect A5): every browser engine in
        # the pool except camoufox is Chromium-based, so a family-agnostic
        # ``select_persona`` gives ~50% of domains a Firefox/Safari persona on a
        # Chromium engine — an instant cross-check failure (UA says Firefox but
        # navigator.userAgentData/window.chrome say Chromium). When the caller
        # knows the real engine family it passes it; otherwise Chromium is the
        # only coherent default. The adaptive controller re-aligns the persona
        # via ``set_browser_family`` once a non-Chromium engine is selected.
        persona = select_persona(domain, browser_family or "chromium")
        pf = run_preflight(url, config=config)
        try:
            proxy = resolve_bypass("proxy")
        except (ValueError, KeyError):
            proxy = None
        try:
            residential_proxy = resolve_bypass(
                "residential_proxy",
                bypass_config=config,
            )
        except (ValueError, KeyError):
            residential_proxy = None
        return cls(
            persona=persona,
            preflight=pf,
            domain=domain,
            proxy=proxy,
            residential_proxy=residential_proxy,
        )

    async def before_request(self, url: str) -> dict[str, str]:
        """Apply timing humanization and return extra HTTP headers.

        When an enforced robots.txt policy disallowed this source, fail fast
        before any network work (Wave 5.1). Enforcement is opt-in, so this
        never fires under the default advisory configuration.
        """
        if self.robots_blocked:
            from job_ftch.infrastructure.bypass.robots_policy import RobotsDisallowedError

            raise RobotsDisallowedError(f"robots.txt policy disallows fetching {self._domain}")
        pacer = get_domain_pacer()
        domain = urlparse(url).netloc.lower()
        await pacer.acquire(domain)
        humanizer = get_humanizer()
        await humanizer.pre_request_delay(url)
        return humanizer.build_headers(url, self.persona.http_headers())

    async def on_page(self, page: Any) -> None:
        """Apply stealth hardening JS scripts to a browser page.

        Must be called BEFORE first navigation (page.goto).
        """
        await apply_persona_hardening(page, self.persona)
        self._debug_selfcheck()

    def _debug_selfcheck(self) -> None:
        """Log any declared-identity incoherence when the debug flag is on.

        Opt-in via ``JOB_FTCH_BYPASS_IDENTITY_SELFCHECK``. Never raises in
        production - a leak here is a code defect to surface, not a reason to
        abort a crawl.
        """
        try:
            from job_ftch.config import get_settings

            if not getattr(get_settings(), "bypass_identity_selfcheck", False):
                return
            from job_ftch.infrastructure.bypass.identity.coherence import check_identity

            report = check_identity(self.persona)
            if not report.ok:
                logger.warning(
                    "bypass_identity_incoherent",
                    persona=getattr(self.persona, "name", "?"),
                    issues=[f"{i.code}:{i.axis}" for i in report.issues],
                )
        except Exception:  # never let a debug check affect a crawl
            logger.debug("bypass_identity_selfcheck_failed")

    def record_success(self, url: str) -> None:
        domain = urlparse(url).netloc.lower()
        router = get_router()
        router.record_success(url, self.tier)
        intel = get_domain_intel()
        intel.record_success(domain, self.tier, network=self._network)

    def record_failure(self, url: str, kind: str = "blocked") -> None:
        domain = urlparse(url).netloc.lower()
        router = get_router()
        router.record_failure(url, self.tier)
        intel = get_domain_intel()
        intel.record_failure_kind(domain, self.tier, kind, network=self._network)
        logger.debug("bypass_context_failure", domain=domain, tier=self.tier, kind=kind)

    def context_kwargs(self, *, use_proxy: bool = False) -> dict[str, Any]:
        """Persona-derived kwargs for Playwright new_context, with proxy overlay."""
        kw = self.persona.context_kwargs()
        active = self._active_proxy if use_proxy else None
        if active and hasattr(active, "apply_browser_args"):
            kw["_domain_hint"] = self._domain
            kw = active.apply_browser_args(kw)
            # A proxy that ignores the hint (datacenter tier) leaves it in the
            # dict; it must not reach Playwright new_context() as a kwarg.
            kw.pop("_domain_hint", None)
        return kw

    def apply_browser_args(
        self,
        kwargs: dict[str, Any],
        *,
        use_proxy: bool = False,
    ) -> dict[str, Any]:
        """Implement BypassStrategy Protocol: modify Playwright launch() kwargs.

        Persona settings (user_agent, viewport, locale, timezone_id) belong to
        `new_context()`, not `launch()` - callers get those via `context_kwargs()`
        instead. Merging them here made `browser_type.launch()` raise
        `unexpected keyword argument 'user_agent'` on every stealth_browser
        escalation.
        """
        merged = dict(kwargs)
        active = self._active_proxy if use_proxy else None
        if active and hasattr(active, "apply_browser_args"):
            merged["_domain_hint"] = self._domain
            merged = active.apply_browser_args(merged)
            # Strip the hint if the proxy tier ignored it (datacenter tier),
            # so it never reaches browser_type.launch() as a kwarg.
            merged.pop("_domain_hint", None)
        return merged

    async def apply_page(self, page: Any) -> None:
        """Implement BypassStrategy Protocol: apply stealth hardening to page."""
        await apply_persona_hardening(
            page,
            self.persona,
            proxy_active=self._network in ("proxy", "residential_proxy"),
        )

    async def apply_http(self, client: Any, *, use_proxy: bool = False) -> Any:
        """Decorate an HTTP client with proxy routing and FingerprintProfile headers."""
        active = self._active_proxy if use_proxy else None
        if active and hasattr(active, "apply_http"):
            with contextlib.suppress(AttributeError, TypeError):
                client._domain_hint = self._domain
            client = await active.apply_http(client)

        from job_ftch.infrastructure.bypass.fingerprint_profile import FingerprintProfile

        profile = FingerprintProfile.from_persona(self.persona)
        headers = profile.http_headers()

        if hasattr(client, "headers"):
            for key, value in headers.items():
                client.headers.setdefault(key, value)

        return client
