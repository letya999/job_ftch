"""Discover canonical job pages from rocketjobs.pl."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse, urlunparse

from selectolax.parser import HTMLParser

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import safe_fetch

if TYPE_CHECKING:
    from job_ftch.domain.source_spec import CareerSiteSpec


class RocketJobsParser:
    """Trust the provider's ``/oferta-pracy/<slug>`` detail links."""

    domain_pattern = r"^https?://(?:www\.)?rocketjobs\.pl(?:[/?#]|$)"
    has_custom_parse = True
    supports_discover = True
    _detail_re = re.compile(r"/oferta-pracy/[a-z0-9-]+/?$", re.IGNORECASE)
    _allowed_hosts = frozenset({"rocketjobs.pl", "www.rocketjobs.pl"})

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(include_if_detail_page=True)

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    @classmethod
    def _canonical_detail_url(cls, url: str) -> str | None:
        parsed = urlparse(url)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in cls._allowed_hosts
            or not cls._detail_re.fullmatch(parsed.path)
        ):
            return None
        return urlunparse(parsed._replace(query="", fragment=""))

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        response = await safe_fetch(client, spec.url)
        current_url = str(response.url)
        current_detail_url = self._canonical_detail_url(current_url)
        if current_detail_url is not None:
            return [current_detail_url]

        urls: list[str] = []
        seen: set[str] = set()
        for anchor in HTMLParser(str(response.text)).css("a[href]"):
            href = anchor.attributes.get("href")
            if not href:
                continue
            canonical_url = self._canonical_detail_url(urljoin(current_url, href))
            if canonical_url is None or canonical_url in seen:
                continue
            seen.add(canonical_url)
            urls.append(canonical_url)
            if len(urls) >= (spec.limit or 50):
                break
        return urls

    @property
    def __name__(self) -> str:
        return "RocketJobsParser"


register_site_parser(
    "rocketjobs",
    domain_pattern=RocketJobsParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:rocketjobs.pl",
        has_stable_url=True,
        supports_ordered_head=False,
        can_detect_freshness_without_snapshot=False,
        ordered_by_newest=False,
        requires_full_snapshot=False,
        rationale="RocketJobs exposes stable, SSR vacancy URLs under /oferta-pracy/.",
    ),
)(RocketJobsParser)
