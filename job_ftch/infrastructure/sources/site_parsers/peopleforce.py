"""Discover vacancy detail pages from PeopleForce career portals."""

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


class PeopleForceCareerParser:
    """Use the provider's canonical ``/careers/v/<id>-<slug>`` URLs."""

    domain_pattern = r"^https?://peopleforce\.softconstruct\.com/careers(?:[/?#]|$)"
    has_custom_parse = True
    supports_discover = True
    _detail_re = re.compile(r"/careers/v/\d+-[a-z0-9-]+/?$", re.IGNORECASE)

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(include_if_detail_page=True)

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        response = await safe_fetch(client, spec.url)
        current_url = str(response.url)
        if self._detail_re.fullmatch(urlparse(current_url).path):
            return [current_url.split("?", 1)[0]]

        tree = HTMLParser(str(response.text))
        urls: list[str] = []
        seen: set[str] = set()
        limit = spec.limit or 50
        source_host = urlparse(current_url).netloc
        for anchor in tree.css("a[href]"):
            href = anchor.attributes.get("href")
            if not href:
                continue
            parsed = urlparse(urljoin(current_url, href))
            if parsed.netloc != source_host or not self._detail_re.fullmatch(parsed.path):
                continue
            canonical_url = urlunparse(parsed._replace(query="", fragment=""))
            if canonical_url in seen:
                continue
            seen.add(canonical_url)
            urls.append(canonical_url)
            if len(urls) >= limit:
                break
        return urls

    @property
    def __name__(self) -> str:
        return "PeopleForceCareerParser"


register_site_parser(
    "peopleforce_career",
    domain_pattern=PeopleForceCareerParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:peopleforce.softconstruct.com",
        has_stable_url=True,
        supports_ordered_head=False,
        can_detect_freshness_without_snapshot=False,
        ordered_by_newest=False,
        requires_full_snapshot=False,
        rationale="PeopleForce exposes stable vacancy detail URLs in its listing HTML.",
    ),
)(PeopleForceCareerParser)
