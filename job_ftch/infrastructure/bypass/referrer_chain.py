"""Referrer chain forgery for organic navigation simulation.

Generates realistic referrer chains simulating how real users navigate
to career sites. Users rarely arrive directly — they come via search
engines, social networks, or email links.

Direct navigation is a strong bot signal. Realistic referrer chains
evade direct-navigation detection.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class Referrer:
    """A single referrer in the navigation chain."""

    url: str
    timestamp_offset: float  # Seconds before current time (negative = past)
    source_type: str  # "search", "social", "email", "direct", "intermediate"


# Referrer pools by source type
_SEARCH_ENGINES: list[tuple[str, str]] = [
    ("https://www.google.com/search?q={query}", "google"),
    ("https://www.bing.com/search?q={query}", "bing"),
    ("https://duckduckgo.com/?q={query}", "duckduckgo"),
    ("https://www.google.ru/search?q={query}", "google_ru"),
    ("https://yandex.ru/search/?text={query}", "yandex"),
]

_SOCIAL_NETWORKS: list[tuple[str, str]] = [
    ("https://www.linkedin.com/jobs/search/?keywords={query}", "linkedin"),
    ("https://twitter.com/search?q={query}", "twitter"),
    ("https://www.facebook.com/search/posts/?q={query}", "facebook"),
    ("https://www.reddit.com/search/?q={query}", "reddit"),
]

_EMAIL_PROVIDERS: list[tuple[str, str]] = [
    ("https://mail.google.com/mail/u/0/#inbox", "gmail"),
    ("https://outlook.live.com/mail/0/inbox", "outlook"),
    ("https://mail.yahoo.com/", "yahoo"),
]

_INTERMEDIATE_SITES: list[tuple[str, str]] = [
    ("https://www.indeed.com/", "indeed"),
    ("https://www.glassdoor.com/", "glassdoor"),
    ("https://stackoverflow.com/jobs", "stackoverflow"),
    ("https://github.com/jobs", "github"),
    ("https://news.ycombinator.com/", "hackernews"),
]

# Search query templates for job-related searches
_JOB_SEARCH_QUERIES: list[str] = [
    "software engineer jobs",
    "python developer vacancies",
    "remote developer jobs",
    "backend engineer positions",
    "full stack developer careers",
    "senior software engineer",
    "tech jobs near me",
    "developer opportunities",
]


class ReferrerChainGenerator:
    """Generate realistic referrer chains for organic navigation simulation.

    Usage:
        gen = ReferrerChainGenerator(seed=42)
        chain = gen.generate_chain("https://example.com/jobs")
        # Returns: [Referrer(google), Referrer(indeed), Referrer(target)]
    """

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)

    def generate_chain(
        self,
        target_url: str,
        *,
        min_hops: int = 2,
        max_hops: int = 4,
    ) -> list[Referrer]:
        """Generate a realistic referrer chain ending at target_url.

        Args:
            target_url: Final destination URL
            min_hops: Minimum number of intermediate referrers (default 2)
            max_hops: Maximum number of intermediate referrers (default 4)

        Returns:
            List of Referrer objects in chronological order (newest first)
        """
        num_hops = self._rng.randint(min_hops, max_hops)
        chain: list[Referrer] = []

        # Generate intermediate referrers
        current_time_offset = 0.0
        for _i in range(num_hops):
            referrer = self._generate_intermediate_referrer(current_time_offset)
            chain.append(referrer)
            # Time between hops: 30 seconds to 5 minutes
            time_gap = self._rng.uniform(30, 300)
            current_time_offset -= time_gap

        # Add final target as last referrer
        chain.append(
            Referrer(
                url=target_url,
                timestamp_offset=current_time_offset - self._rng.uniform(10, 60),
                source_type="target",
            )
        )

        return chain

    def _generate_intermediate_referrer(self, time_offset: float) -> Referrer:
        """Generate a single intermediate referrer."""
        # Weighted random selection of source type
        source_type = self._rng.choices(
            ["search", "social", "email", "intermediate"],
            weights=[0.5, 0.2, 0.1, 0.2],  # 50% search, 20% social, etc.
            k=1,
        )[0]

        if source_type == "search":
            template, _ = self._rng.choice(_SEARCH_ENGINES)
            query = self._rng.choice(_JOB_SEARCH_QUERIES)
            url = template.format(query=query.replace(" ", "+"))
        elif source_type == "social":
            template, _ = self._rng.choice(_SOCIAL_NETWORKS)
            query = self._rng.choice(_JOB_SEARCH_QUERIES)
            url = template.format(query=query.replace(" ", "+"))
        elif source_type == "email":
            template, _ = self._rng.choice(_EMAIL_PROVIDERS)
            url = template
        else:  # intermediate
            template, _ = self._rng.choice(_INTERMEDIATE_SITES)
            url = template

        return Referrer(
            url=url,
            timestamp_offset=time_offset,
            source_type=source_type,
        )

    def get_immediate_referrer(self, target_url: str) -> Referrer:
        """Get a single immediate referrer (no chain, just one hop).

        Useful for simpler scenarios where full chain is overkill.
        """
        return self._generate_intermediate_referrer(0.0)

    def apply_to_page(self, page: Any, referrer: Referrer) -> None:
        """Apply referrer to a Playwright page before navigation.

        Sets the Referer header and uses page.goto() with referrer parameter.
        """
        # Note: This is a helper method. Actual application happens in
        # BrowserSessionBypass.open_page() where we have access to the page.
        pass


def extract_domain(url: str) -> str:
    """Extract domain from URL for consistency checks."""
    parsed = urlparse(url)
    return parsed.netloc or parsed.path


def is_same_domain(url1: str, url2: str) -> bool:
    """Check if two URLs are from the same domain."""
    return extract_domain(url1) == extract_domain(url2)
