"""Request timing humanization and referer chain simulation.

Phase 2.2: Adds random delays between requests to the same domain,
simulating human think-time after page loads.

Phase 2.3: Generates realistic Referer headers:
- First visit to a domain: Google search referer
- Known ATS domains: company career page as referer
- Subsequent pages: previous page URL as referer
"""

from __future__ import annotations

import asyncio
import random
import time
from urllib.parse import urlparse

import structlog

logger = structlog.get_logger("job_ftch.bypass.humanize")

_ATS_REFERER_PATTERNS: dict[str, str] = {
    "boards.greenhouse.io": "https://www.google.com/search?q={company}+jobs",
    "jobs.lever.co": "https://www.google.com/search?q={company}+careers",
    "jobs.ashbyhq.com": "https://www.google.com/search?q={company}+careers",
    "apply.workable.com": "https://www.google.com/search?q={company}+jobs",
}

_SEARCH_REFERERS: list[str] = [
    "https://www.google.com/",
    "https://www.google.com/search?q=jobs",
    "https://www.google.com/search?q=careers",
    "https://duckduckgo.com/",
]

_MIN_DELAY = 0.8
_MAX_DELAY = 4.0
_THINK_MIN = 0.3
_THINK_MAX = 2.5


class RequestHumanizer:
    """Adds human-like timing jitter and referer chains to requests."""

    def __init__(
        self,
        *,
        min_delay: float = _MIN_DELAY,
        max_delay: float = _MAX_DELAY,
        think_min: float = _THINK_MIN,
        think_max: float = _THINK_MAX,
    ) -> None:
        self._min_delay = min_delay
        self._max_delay = max_delay
        self._think_min = think_min
        self._think_max = think_max
        self._last_request_at: dict[str, float] = {}
        self._last_url: dict[str, str] = {}
        self._listing_url: dict[str, str] = {}

    def _get_domain(self, url: str) -> str:
        return urlparse(url).netloc.lower()

    async def pre_request_delay(self, url: str) -> float:
        """Wait a human-like interval if we recently hit this domain."""
        domain = self._get_domain(url)
        last = self._last_request_at.get(domain, 0.0)
        elapsed = time.monotonic() - last
        target_delay = random.uniform(self._min_delay, self._max_delay)
        remaining = target_delay - elapsed
        if remaining > 0:
            await asyncio.sleep(remaining)
        self._last_request_at[domain] = time.monotonic()
        return max(remaining, 0.0)

    async def post_load_think_time(self) -> float:
        """Simulate human think-time after a page loads."""
        delay = random.uniform(self._think_min, self._think_max)
        await asyncio.sleep(delay)
        return delay

    def set_listing_url(self, domain: str, listing_url: str) -> None:
        """Pin the listing URL as the referer source for this domain's detail pages."""
        self._listing_url[domain] = listing_url

    def get_referer(self, url: str) -> str:
        """Generate a plausible Referer header for the request."""
        domain = self._get_domain(url)

        # For detail pages, prefer the listing URL as referer
        listing = self._listing_url.get(domain)
        if listing and url != listing:
            self._last_url[domain] = url
            return listing

        prev = self._last_url.get(domain)
        if prev:
            self._last_url[domain] = url
            return prev

        for ats_pattern, referer_tpl in _ATS_REFERER_PATTERNS.items():
            if ats_pattern in domain:
                company = domain.split(".")[0].replace("jobs", "").replace("boards", "").strip("-")
                if not company:
                    company = "company"
                self._last_url[domain] = url
                return referer_tpl.format(company=company)

        self._last_url[domain] = url
        return random.choice(_SEARCH_REFERERS)

    def build_headers(self, url: str, base: dict[str, str] | None = None) -> dict[str, str]:
        """Build request headers with Referer injected."""
        headers = dict(base) if base else {}
        if "Referer" not in headers:
            headers["Referer"] = self.get_referer(url)
        return headers

    def reset(self, domain: str | None = None) -> None:
        if domain:
            self._last_request_at.pop(domain, None)
            self._last_url.pop(domain, None)
        else:
            self._last_request_at.clear()
            self._last_url.clear()


_humanizer: RequestHumanizer | None = None


def get_humanizer() -> RequestHumanizer:
    global _humanizer
    if _humanizer is None:
        _humanizer = RequestHumanizer()
    return _humanizer
