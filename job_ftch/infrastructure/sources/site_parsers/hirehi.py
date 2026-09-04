"""Runtime defaults and search URL construction for hirehi.ru."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse, urlunparse

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    normalize_search_keywords,
    with_query_params,
)

_URL_FILTER = r"hirehi\.ru/[a-z0-9-]+/[a-z0-9-]+-\d+/?$"


class HireHiParser:
    """Use HireHi's server-rendered `?search=` surface with one role per URL."""

    domain_pattern = r"^https?://(?:www\.)?hirehi\.ru(?:/|$)"
    has_custom_parse = False
    supports_search = True
    search_mode = "per_keyword"

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            url_filter=_URL_FILTER,
            include_if_detail_page=True,
            extra={
                "pagination": {
                    "param_name": "page",
                    "start": 2,
                    "increment": 1,
                    "max_pages": 5,
                }
            },
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    def build_search_urls(
        self,
        base_url: str,
        keywords: Any,
        *,
        limit: int | None = None,
    ) -> list[str]:
        del limit
        terms = normalize_search_keywords(keywords)
        if not terms:
            return []
        parsed = urlparse(base_url)
        if parsed.path.rstrip("/").startswith("/vacancies/"):
            parsed = parsed._replace(path="/")
        listing_url = urlunparse(parsed)
        return [with_query_params(listing_url, {"search": term}) for term in terms]

    @property
    def __name__(self) -> str:
        return "HireHiParser"


register_site_parser(
    "hirehi",
    domain_pattern=HireHiParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:hirehi.ru",
        has_stable_url=True,
        supports_ordered_head=True,
        can_detect_freshness_without_snapshot=False,
        ordered_by_newest=True,
        requires_full_snapshot=False,
        rationale="HireHi exposes a server-rendered search query and JSON-LD vacancy links.",
    ),
)(HireHiParser)
