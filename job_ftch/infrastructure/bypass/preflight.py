"""Smart pre-flight pipeline for bypass tier selection.

Before hitting a domain, runs through a fast decision cascade:
1. Check domain_intel cache → use known-good tier
2. Check known ATS fingerprint → use dedicated monitor (skip bypass entirely)
3. Consult RiskRouter → pre-select tier by domain reputation
4. Otherwise → fall through to AdaptiveBypassManager default escalation

This replaces the naive "always start at noop and escalate" pattern
for domains we already have intelligence about.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import structlog

from job_ftch.application.registry import get_bypass_capability, resolve_bypass

logger = structlog.get_logger("job_ftch.bypass.preflight")

_ATS_DOMAINS: dict[str, str] = {
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
    "jobs.ashbyhq.com": "ashby",
    "apply.workable.com": "workable",
    "jobs.smartrecruiters.com": "smartrecruiters",
    "careers.personio.de": "personio",
    "app.rippling.com": "rippling",
}

_RESERVED_TEST_DOMAINS = frozenset({"example.com", "example.net", "example.org", "localhost"})

_robots_policy: Any = None


def get_robots_policy() -> Any:
    """Singleton robots.txt policy seeded with known ATS domains (Wave 5.1).

    ATS boards are always exempt; enforcement is opt-in via
    ``JOB_FTCH_ROBOTS_ENFORCE`` (advisory log-only otherwise).
    """
    global _robots_policy
    if _robots_policy is None:
        from job_ftch.config import get_settings
        from job_ftch.infrastructure.bypass.robots_policy import RobotsPolicy

        _robots_policy = RobotsPolicy(
            enforce=getattr(get_settings(), "robots_enforce", False),
            ats_domains=frozenset(_ATS_DOMAINS),
        )
    return _robots_policy


class PreflightResult:
    """Result of pre-flight decision."""

    __slots__ = ("tier", "network", "reason", "bypass", "skip_bypass", "robots_blocked")

    def __init__(
        self,
        tier: str,
        reason: str,
        bypass: Any = None,
        *,
        network: str = "direct",
        skip_bypass: bool = False,
        robots_blocked: bool = False,
    ) -> None:
        self.tier = tier
        self.reason = reason
        self.bypass = bypass
        self.network = network
        self.skip_bypass = skip_bypass
        self.robots_blocked = robots_blocked


def run_preflight(url: str, *, config: dict[str, Any] | None = None) -> PreflightResult:
    """Run pre-flight checks and return a bypass tier recommendation.

    Returns PreflightResult with the selected tier, reason, and optionally
    a pre-resolved bypass instance. If skip_bypass is True, the caller
    should use a dedicated monitor instead of bypass.
    """
    domain = urlparse(url).netloc.lower()
    config = config or {}

    if (
        domain in _RESERVED_TEST_DOMAINS
        or domain.endswith(".test")
        or domain.endswith(".localhost")
    ):
        return PreflightResult(tier="adaptive", reason="reserved_test_domain")

    for ats_domain, ats_name in _ATS_DOMAINS.items():
        if ats_domain in domain:
            return PreflightResult(
                tier=ats_name,
                reason=f"known_ats:{ats_name}",
                skip_bypass=True,
            )

    from job_ftch.infrastructure.bypass.domain_intel import get_domain_intel

    intel = get_domain_intel()

    # Wave 5.1: robots.txt policy gate (advisory unless JOB_FTCH_ROBOTS_ENFORCE=1).
    robots = get_robots_policy()
    if robots.enforce:
        verdict = robots.check(url, domain_intel=intel.get(domain))
        if not verdict.allowed:
            logger.warning("preflight_robots_blocked", domain=domain, reason=verdict.reason)
            return PreflightResult(
                tier="noop",
                reason=f"robots:{verdict.reason}",
                robots_blocked=True,
                skip_bypass=True,
            )

    # Wave 4.3: CDP-sensitive routing
    entry = intel.get(domain)
    if entry.known_vendor:
        vendor_lower = entry.known_vendor.lower()
        if vendor_lower in {"datadome", "perimeterx", "px"}:
            from job_ftch.application.registry import list_bypass_capabilities

            available = list_bypass_capabilities()
            cdp_free_tier = next((t for t in ("nodriver", "camoufox") if t in available), None)
            if cdp_free_tier:
                logger.debug(
                    "preflight_vendor_route", domain=domain, vendor=vendor_lower, tier=cdp_free_tier
                )
                return PreflightResult(
                    tier=cdp_free_tier,
                    reason=f"known_vendor:{vendor_lower}",
                    bypass=resolve_bypass(cdp_free_tier, config),
                )

    cached_route = intel.get_recommended_route(domain)
    if cached_route:
        cached_tier, cached_network = cached_route
        try:
            capability = get_bypass_capability(cached_tier)
            if capability.legal_gate:
                raw_gate = config.get(f"allow_{capability.legal_gate}", True)
                if isinstance(raw_gate, str):
                    allowed = raw_gate.strip().lower() not in {"0", "false", "no", "off"}
                else:
                    allowed = bool(raw_gate)
                if not allowed:
                    raise ValueError("cached capability is disabled by policy")
            bypass = resolve_bypass(cached_tier, config)
            if cached_network == "proxy":
                resolve_bypass("proxy")
            logger.debug(
                "preflight_cached_route",
                domain=domain,
                tier=cached_tier,
                network=cached_network,
            )
            return PreflightResult(
                tier=cached_tier,
                network=cached_network,
                reason=f"domain_intel_cache:{cached_tier}",
                bypass=bypass,
            )
        except Exception:
            pass

    from job_ftch.infrastructure.bypass.risk_router import get_router

    router = get_router()
    tier = router.select_tier(url)
    try:
        bypass = resolve_bypass(tier, config)
        logger.debug("preflight_risk_routed", domain=domain, tier=tier)
        return PreflightResult(
            tier=tier,
            reason=f"risk_router:{tier}",
            bypass=bypass,
        )
    except Exception:
        pass

    return PreflightResult(
        tier="adaptive",
        reason="fallback_adaptive",
    )
