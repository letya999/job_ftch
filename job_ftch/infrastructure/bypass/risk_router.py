"""Domain reputation-based risk routing for bypass tier pre-selection.

Instead of always starting at the cheapest tier and escalating through
failures, RiskRouter uses historical failure data (EMA-smoothed) to
pre-select the appropriate tier for a domain on first contact.

Risk scoring:
- risk < 0.15 → curl_stealth (lightweight, fast)
- risk < 0.50 → stealth_browser (medium weight)
- risk >= 0.50 → nodriver/cloak (heavy, maximum stealth)

The reputation decays over time (half-life configurable) so domains
that improve their bot detection get gradually downgraded to cheaper tiers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import structlog

from job_ftch.application.registry import resolve_bypass

logger = structlog.get_logger("job_ftch.bypass.risk_router")

_DEFAULT_DECAY_HALF_LIFE = 86400.0  # 24 hours
_DEFAULT_EMA_ALPHA = 0.3
_MIN_PRESELECT_OBSERVATIONS = 3


@dataclass(slots=True)
class DomainReputation:
    """EMA-based domain risk score with time decay."""

    domain: str
    risk_score: float = 0.0
    total_successes: int = 0
    total_failures: int = 0
    last_success_at: float = 0.0
    last_failure_at: float = 0.0
    last_tier: str = ""
    known_vendor: str = ""

    def record_success(self, tier: str, alpha: float = _DEFAULT_EMA_ALPHA) -> None:
        self.total_successes += 1
        self.last_success_at = time.time()
        self.last_tier = tier
        self.risk_score += alpha * (0.0 - self.risk_score)

    def record_failure(self, tier: str, alpha: float = _DEFAULT_EMA_ALPHA) -> None:
        self.total_failures += 1
        self.last_failure_at = time.time()
        self.last_tier = tier
        self.risk_score += alpha * (1.0 - self.risk_score)

    def decayed_score(self, half_life: float = _DEFAULT_DECAY_HALF_LIFE) -> float:
        if self.last_failure_at <= 0:
            return self.risk_score
        elapsed = time.time() - self.last_failure_at
        decay = float(0.5 ** (elapsed / half_life))
        return self.risk_score * decay

    @property
    def total_attempts(self) -> int:
        return self.total_successes + self.total_failures


_TIER_THRESHOLDS: list[tuple[float, str]] = [
    (0.15, "curl_stealth"),
    (0.50, "stealth_browser"),
    (1.01, "nodriver"),
]


class RiskRouter:
    """Registry-driven risk-based bypass tier selector.

    Maintains per-domain reputation and uses it to pre-select the
    appropriate bypass tier without escalation warmup.
    """

    def __init__(
        self,
        *,
        alpha: float = _DEFAULT_EMA_ALPHA,
        half_life: float = _DEFAULT_DECAY_HALF_LIFE,
        tier_thresholds: list[tuple[float, str]] | None = None,
    ) -> None:
        self._alpha = alpha
        self._half_life = half_life
        self._thresholds = tier_thresholds or list(_TIER_THRESHOLDS)
        self._reputations: dict[str, DomainReputation] = {}

    def _get_domain(self, url: str) -> str:
        return urlparse(url).netloc.lower()

    def get_reputation(self, url: str) -> DomainReputation:
        domain = self._get_domain(url)
        if domain not in self._reputations:
            self._reputations[domain] = DomainReputation(domain=domain)
        return self._reputations[domain]

    def select_tier(self, url: str) -> str:
        """Select a preflight recommendation only after real observations.

        A fresh domain must begin at the controller's cheap ``noop`` route;
        otherwise this reputation cache becomes a second escalation state
        machine and silently forces curl on every source.
        """
        rep = self.get_reputation(url)
        if rep.total_attempts < _MIN_PRESELECT_OBSERVATIONS:
            return "noop"
        score = rep.decayed_score(self._half_life)
        for threshold, tier in self._thresholds:
            if score < threshold:
                return tier
        return self._thresholds[-1][1]

    def resolve_bypass_for(self, url: str, config: dict[str, Any] | None = None) -> Any:
        """Resolve and return the bypass instance for the URL's risk level."""
        tier = self.select_tier(url)
        try:
            bypass = resolve_bypass(tier, config)
            logger.debug(
                "risk_routed",
                url=url,
                tier=tier,
                risk=self.get_reputation(url).decayed_score(self._half_life),
            )
            return bypass
        except Exception:
            logger.warning("risk_route_fallback", tier=tier, url=url)
            return resolve_bypass("noop")

    def record_success(self, url: str, tier: str) -> None:
        rep = self.get_reputation(url)
        rep.record_success(tier, self._alpha)

    def record_failure(self, url: str, tier: str) -> None:
        rep = self.get_reputation(url)
        rep.record_failure(tier, self._alpha)

    def set_known_vendor(self, url: str, vendor: str) -> None:
        rep = self.get_reputation(url)
        rep.known_vendor = vendor

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "domains_tracked": len(self._reputations),
            "high_risk": sum(
                1 for r in self._reputations.values() if r.decayed_score(self._half_life) >= 0.50
            ),
            "medium_risk": sum(
                1
                for r in self._reputations.values()
                if 0.15 <= r.decayed_score(self._half_life) < 0.50
            ),
            "low_risk": sum(
                1 for r in self._reputations.values() if r.decayed_score(self._half_life) < 0.15
            ),
        }

    def export_reputations(self) -> list[dict[str, Any]]:
        """Export all reputations for persistence."""
        return [
            {
                "domain": r.domain,
                "risk_score": r.risk_score,
                "total_successes": r.total_successes,
                "total_failures": r.total_failures,
                "last_success_at": r.last_success_at,
                "last_failure_at": r.last_failure_at,
                "last_tier": r.last_tier,
                "known_vendor": r.known_vendor,
            }
            for r in self._reputations.values()
        ]

    def import_reputations(self, data: list[dict[str, Any]]) -> None:
        """Import reputations from persisted data."""
        for entry in data:
            domain = entry.get("domain", "")
            if not domain:
                continue
            rep = DomainReputation(
                domain=domain,
                risk_score=entry.get("risk_score", 0.0),
                total_successes=entry.get("total_successes", 0),
                total_failures=entry.get("total_failures", 0),
                last_success_at=entry.get("last_success_at", 0.0),
                last_failure_at=entry.get("last_failure_at", 0.0),
                last_tier=entry.get("last_tier", ""),
                known_vendor=entry.get("known_vendor", ""),
            )
            self._reputations[domain] = rep


_router_instance: RiskRouter | None = None


def get_router(
    *,
    alpha: float = _DEFAULT_EMA_ALPHA,
    half_life: float = _DEFAULT_DECAY_HALF_LIFE,
) -> RiskRouter:
    """Get or create the singleton RiskRouter instance."""
    global _router_instance
    if _router_instance is None:
        _router_instance = RiskRouter(alpha=alpha, half_life=half_life)
    return _router_instance
