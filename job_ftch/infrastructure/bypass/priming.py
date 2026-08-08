"""Background priming: warm clearance sessions ahead of the live crawl (TRACK C).

Opt-in and polite. For configured source domains with cold or near-expiry
profiles, this visits the domain root at the DomainPacer rate (never faster),
lets the free browser_wait tier auto-clear Cloudflare/Turnstile, and leaves the
resulting clearance cookie in the per-domain persistent profile (TRACK B) so the
live crawl presents an already-trusted session instead of paying the challenge
cost inline.

This primes our OWN return-visitor state - it is not an attack. It is gated by
``bypass_background_priming_enabled`` (default off); with the flag off the whole
module is inert and behavior is exactly as before.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import structlog

logger = structlog.get_logger("job_ftch.bypass.priming")

# Same IP-bound clearance allowlist the session-state layer uses.
_CLEARANCE_ALLOWLIST = frozenset({"cf_clearance", "cf_bm", "dd_cookie", "_px3", "datadome"})


def _domain_key(domain: str) -> str:
    return hashlib.sha256(domain.strip().lower().encode("utf-8")).hexdigest()[:16]


class PrimingOutcome(StrEnum):
    """Per-domain result of one priming decision/visit."""

    PRIMED = "primed"  # cold domain warmed for the first time
    REFRESHED = "refreshed"  # near-expiry session refreshed
    SKIPPED_WARM = "skipped_warm"  # still fresh, nothing to do
    FAILED = "failed"  # visit raised / no clearance captured
    BUDGET_EXHAUSTED = "budget_exhausted"  # per-cycle cap reached
    DISABLED = "disabled"  # feature flag off


@dataclass(slots=True)
class PrimingState:
    """Readable freshness sidecar for one domain."""

    domain: str
    last_primed: float = 0.0
    clearance_expires: float = 0.0  # 0.0 => unknown / session cookie only

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PrimingState:
        return cls(
            domain=str(data.get("domain", "")),
            last_primed=float(data.get("last_primed", 0.0) or 0.0),
            clearance_expires=float(data.get("clearance_expires", 0.0) or 0.0),
        )


@dataclass(slots=True)
class PrimingReport:
    """Aggregate counters for one priming cycle."""

    primed: int = 0
    refreshed: int = 0
    skipped_warm: int = 0
    failed: int = 0
    budget_exhausted: int = 0
    disabled: bool = False
    # Freshness pre-check outcomes (a "hit" is a domain already warm enough).
    primed_hit: int = 0
    primed_miss: int = 0
    prefetched: dict[str, list[str]] = field(default_factory=dict)

    def record(self, outcome: PrimingOutcome) -> None:
        if outcome is PrimingOutcome.PRIMED:
            self.primed += 1
            self.primed_miss += 1
        elif outcome is PrimingOutcome.REFRESHED:
            self.refreshed += 1
            self.primed_miss += 1
        elif outcome is PrimingOutcome.SKIPPED_WARM:
            self.skipped_warm += 1
            self.primed_hit += 1
        elif outcome is PrimingOutcome.FAILED:
            self.failed += 1
        elif outcome is PrimingOutcome.BUDGET_EXHAUSTED:
            self.budget_exhausted += 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PrimingDecision:
    """Whether a domain should be primed now, and why."""

    should_prime: bool
    outcome: PrimingOutcome  # PRIMED (cold), REFRESHED (near-expiry) or SKIPPED_WARM
    state: PrimingState


class BackgroundPrimer:
    """Warms clearance sessions for cold / near-expiry domains, politely."""

    def __init__(
        self,
        settings: Any,
        *,
        pacer: Any = None,
        clock: Any = time.time,
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._enabled = bool(getattr(settings, "bypass_background_priming_enabled", False))
        self._state_dir = Path(
            getattr(settings, "bypass_priming_state_dir", Path(".runtime/priming"))
        )
        self._refresh_window = int(getattr(settings, "bypass_priming_refresh_window_seconds", 600))
        self._max_domains = int(getattr(settings, "bypass_priming_max_domains_per_cycle", 20))
        self._settle_seconds = float(getattr(settings, "bypass_priming_settle_seconds", 6.0))
        self._min_interval = int(getattr(settings, "bypass_priming_min_interval_seconds", 1800))
        self._prefetch = bool(getattr(settings, "bypass_priming_prefetch_listings", False))
        if pacer is None:
            from job_ftch.infrastructure.bypass.pacing import DomainPacer

            pacer = DomainPacer(float(getattr(settings, "bypass_default_requests_per_second", 2.0)))
        self._pacer = pacer

    # ── freshness ──────────────────────────────────────────────────────
    def _state_path(self, domain: str) -> Path:
        return self._state_dir / f"{_domain_key(domain)}.json"

    def load_state(self, domain: str) -> PrimingState:
        path = self._state_path(domain)
        if not path.exists():
            return PrimingState(domain=domain)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return PrimingState(domain=domain)
        if not isinstance(data, dict):
            return PrimingState(domain=domain)
        return PrimingState.from_dict(data)

    def _save_state(self, state: PrimingState) -> None:
        path = self._state_path(state.domain)
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            if os.name != "nt":
                os.chmod(self._state_dir, 0o700)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            logger.debug("bypass_priming_state_save_failed", domain=state.domain)

    def needs_priming(self, domain: str) -> PrimingDecision:
        """Decide whether ``domain`` should be primed now."""
        state = self.load_state(domain)
        now = float(self._clock())
        if state.last_primed <= 0.0:
            return PrimingDecision(True, PrimingOutcome.PRIMED, state)  # never primed => cold
        if state.clearance_expires > 0.0:
            if now >= state.clearance_expires - self._refresh_window:
                return PrimingDecision(True, PrimingOutcome.REFRESHED, state)  # near expiry
            return PrimingDecision(False, PrimingOutcome.SKIPPED_WARM, state)
        # Unknown expiry (session cookie only): refresh once past the min interval.
        if now - state.last_primed >= self._min_interval:
            return PrimingDecision(True, PrimingOutcome.REFRESHED, state)
        return PrimingDecision(False, PrimingOutcome.SKIPPED_WARM, state)

    def freshness(self, domain: str) -> PrimingOutcome:
        """Pre-check outcome an operator can read (hit == already warm)."""
        return self.needs_priming(domain).outcome

    # ── priming cycle ──────────────────────────────────────────────────
    async def prime_domains(self, domains: Any) -> PrimingReport:
        """Prime the given domains, politely and budget-capped."""
        report = PrimingReport()
        if not self._enabled:
            report.disabled = True
            logger.debug("bypass_priming_disabled")
            return report
        seen: set[str] = set()
        visited = 0
        for raw in domains:
            domain = _normalize_domain(str(raw))
            if not domain or domain in seen:
                continue
            seen.add(domain)
            decision = self.needs_priming(domain)
            if not decision.should_prime:
                report.record(PrimingOutcome.SKIPPED_WARM)
                continue
            if visited >= self._max_domains:
                report.record(PrimingOutcome.BUDGET_EXHAUSTED)
                continue
            visited += 1
            await self._pacer.acquire(domain)
            outcome = await self._prime_one(domain, decision.outcome)
            report.record(outcome)
            if self._prefetch:
                urls = await self.prefetch_listings(domain)
                if urls:
                    report.prefetched[domain] = urls
        logger.info("bypass_priming_cycle", **_report_log_fields(report))
        return report

    async def _prime_one(self, domain: str, intended: PrimingOutcome) -> PrimingOutcome:
        """Visit the domain root, settle, capture clearance expiry, persist state."""
        root = f"https://{domain}/"
        try:
            cookies = await self._warm_and_read_cookies(root, domain)
        except Exception as exc:  # one domain must never abort the cycle
            logger.warning("bypass_priming_visit_failed", domain=domain, error=type(exc).__name__)
            return PrimingOutcome.FAILED
        expires = _min_clearance_expiry(cookies)
        has_clearance = any(str(c.get("name", "")).lower() in _CLEARANCE_ALLOWLIST for c in cookies)
        if not has_clearance:
            logger.info("bypass_priming_no_clearance", domain=domain)
            return PrimingOutcome.FAILED
        self._save_state(
            PrimingState(domain=domain, last_primed=float(self._clock()), clearance_expires=expires)
        )
        logger.info("bypass_priming_ok", domain=domain, outcome=intended.value)
        return intended

    async def _warm_and_read_cookies(self, root: str, domain: str) -> list[dict[str, Any]]:
        """Open a persistent-context page at the root, settle, return its cookies."""
        import asyncio

        from job_ftch.application.registry import resolve_bypass
        from job_ftch.infrastructure.bypass.context import BypassContext
        from job_ftch.infrastructure.network.ssrf_guard import check_ssrf
        from job_ftch.infrastructure.sources.browser_utils import open_page

        await check_ssrf(root)
        strategy = resolve_bypass("auto", getattr(self._settings, "bypass_config", {}) or {})
        try:
            ctx = await BypassContext.for_url(root, config={})
            bind = getattr(strategy, "bind_context", None)
            if callable(bind):
                bind(ctx)
        except Exception:
            pass
        config: dict[str, Any] = {
            "url": root,
            "warmup_url": root,
            "persistent_context": True,
        }
        prepare = getattr(strategy, "prepare_browser_config", None)
        if callable(prepare):
            config = prepare(config)
        async with open_page(config, bypass_strategy=strategy) as page:
            if self._settle_seconds > 0:
                await asyncio.sleep(self._settle_seconds)
            return await _read_page_cookies(page)

    async def prefetch_listings(self, domain: str, seed_urls: Any = None) -> list[str]:
        """Opt-in, flag-gated hook for pre-discovering listing URLs (C4).

        Discovery itself is delegated to the universal-fetch layer; this only
        provides the polite, gated seam and returns any explicitly seeded URLs.
        """
        if not self._prefetch:
            return []
        return [str(u) for u in (seed_urls or []) if str(u).strip()]


def _normalize_domain(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if "://" in value:
        value = urlsplit(value).netloc or urlsplit(value).path
    value = value.split("/")[0]
    return value.lower().strip()


def _min_clearance_expiry(cookies: list[dict[str, Any]]) -> float:
    """Smallest positive ``expires`` among clearance cookies (0.0 if none)."""
    expiries: list[float] = []
    for cookie in cookies:
        if str(cookie.get("name", "")).lower() not in _CLEARANCE_ALLOWLIST:
            continue
        try:
            expires = float(cookie.get("expires", 0) or 0)
        except (TypeError, ValueError):
            continue
        if expires > 0:
            expiries.append(expires)
    return min(expiries) if expiries else 0.0


async def _read_page_cookies(page: Any) -> list[dict[str, Any]]:
    raw: Any = None
    exporter = getattr(page, "export_cookies", None)
    if callable(exporter):
        raw = await exporter()
    else:
        context = getattr(page, "context", None)
        cookies = getattr(context, "cookies", None)
        if callable(cookies):
            raw = await cookies()
    if not isinstance(raw, list):
        return []
    return [dict(c) for c in raw if isinstance(c, dict)]


def _report_log_fields(report: PrimingReport) -> dict[str, Any]:
    fields = report.to_dict()
    fields.pop("prefetched", None)
    return fields
