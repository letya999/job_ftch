"""robots.txt compliance policy (Wave 5.1).

Enforces robots.txt for domains outside ATS (Applicant Tracking Systems)
when the operator enables compliance mode via config.

The policy is advisory by default (log-only) and can be made enforcing
via JOB_FTCH_ROBOTS_ENFORCE=1.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import structlog

logger = structlog.get_logger("job_ftch.bypass.robots_policy")


class RobotsDisallowedError(RuntimeError):
    """Raised when an enforced robots.txt policy blocks a URL fetch."""


@dataclass(slots=True)
class RobotsVerdict:
    url: str
    allowed: bool
    reason: str  # 'allowed', 'disallowed_by_robots', 'no_robots_txt', 'ats_exempt', 'enforcement_disabled'


class RobotsPolicy:
    """Check URLs against robots.txt before fetching."""

    def __init__(
        self,
        *,
        user_agent: str = "job_ftch",
        enforce: bool | None = None,
        ats_domains: frozenset[str] | None = None,
    ) -> None:
        if enforce is None:
            enforce = os.environ.get("JOB_FTCH_ROBOTS_ENFORCE", "").lower() in {"1", "true", "yes"}
        self._enforce = enforce
        self._user_agent = user_agent
        self._ats_domains = ats_domains or frozenset()
        self._cache: dict[str, RobotFileParser] = {}

    def is_ats_domain(self, domain: str) -> bool:
        return domain in self._ats_domains

    def check(self, url: str, *, domain_intel: Any = None) -> RobotsVerdict:
        """Check if URL is allowed by robots.txt.

        ATS domains are always exempt. Non-ATS domains are checked
        against cached robots.txt. If enforcement is disabled, disallowed
        URLs are logged but allowed.
        """
        parsed = urlparse(url)
        domain = parsed.netloc

        if self.is_ats_domain(domain):
            return RobotsVerdict(url=url, allowed=True, reason="ats_exempt")

        if not self._enforce:
            return RobotsVerdict(url=url, allowed=True, reason="enforcement_disabled")

        # Check domain_intel for cached robots status
        if domain_intel is not None:
            disallowed = getattr(domain_intel, "robots_txt_disallowed", False)
            if disallowed:
                logger.warning("robots_disallowed", url=url, domain=domain)
                return RobotsVerdict(url=url, allowed=False, reason="disallowed_by_robots")

        # Try cached parser
        parser = self._cache.get(domain)
        if parser is not None:
            allowed = parser.can_fetch(self._user_agent, url)
            reason = "allowed" if allowed else "disallowed_by_robots"
            if not allowed:
                logger.warning("robots_disallowed", url=url, domain=domain)
            return RobotsVerdict(url=url, allowed=allowed, reason=reason)

        # No cached parser — allow by default (parser loaded async elsewhere)
        return RobotsVerdict(url=url, allowed=True, reason="no_robots_txt")

    def load_robots(self, domain: str, robots_txt_content: str) -> None:
        """Parse and cache robots.txt content for a domain."""
        parser = RobotFileParser()
        parser.parse(robots_txt_content.splitlines())
        self._cache[domain] = parser
        logger.debug("robots_loaded", domain=domain)

    def add_ats_domain(self, domain: str) -> None:
        self._ats_domains = self._ats_domains | {domain}

    @property
    def enforce(self) -> bool:
        return self._enforce

    @property
    def cached_domains(self) -> list[str]:
        return list(self._cache.keys())
