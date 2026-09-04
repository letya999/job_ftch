"""Public T2 hiring is a landing page; HH employer 4219 is the listing."""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse, urlunparse

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import normalize_search_keywords
from job_ftch.infrastructure.sources.site_parsers.hh import HhParser

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

_T2_HH_EMPLOYER_ID = "4219"
_T2_HH_URL = f"https://hh.ru/search/vacancy?employer_id={_T2_HH_EMPLOYER_ID}"
_HH_EMPLOYER_RE = re.compile(
    r"https://(?:[a-z0-9-]+\.)?hh\.ru/employer/\d+[^\"' <]*",
    re.IGNORECASE,
)


def _extract_hh_employer_url(page: str) -> str | None:
    match = _HH_EMPLOYER_RE.search(html.unescape(page).replace(r"\u0026", "&"))
    return match.group(0).rstrip("\\") if match else None


class T2CareerParser:
    domain_pattern = r"^https?://(?:careers\.)?t2\.ru(?:/|$)"
    has_custom_parse = True
    supports_discover = False
    supports_search = True
    search_mode = "combined"
    confirmed_empty_on_empty = True

    def build_search_urls(
        self, base_url: str, keywords: Any, *, limit: int | None = None
    ) -> list[str]:
        del limit
        if not normalize_search_keywords(keywords):
            return []
        # Keep the T2 host so this parser stays selected. HH is queried inside
        # ``parse``; rewriting to hh.ru would dispatch HhParser and lose
        # ``confirmed_empty_on_empty``.
        parsed = urlparse(base_url)
        listing = urlunparse(parsed._replace(query="", fragment=""))
        return [listing or "https://careers.t2.ru/"]

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=False,
            wait="domcontentloaded",
            include_if_detail_page=False,
            extra={
                "proxy_rescue_allow_domains": [
                    "hh.ru",
                    "careers.t2.ru",
                    "t2.ru",
                ],
            },
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return "t2_career"

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        employer_url = _T2_HH_URL
        try:
            response = await client.get(spec.url, follow_redirects=True)
            response.raise_for_status()
            extracted = _extract_hh_employer_url(response.text)
            if extracted:
                employer_url = extracted
        except Exception:  # noqa: BLE001 - HH employer 4219 is the known listing
            pass
        delegated = spec.model_copy(update={"url": employer_url})
        async for item in HhParser().parse(delegated, client):
            metadata = dict(item.metadata or {})
            metadata["parser"] = "t2_hh"
            metadata.setdefault("company", "T2")
            metadata["company_authoritative"] = True
            metadata["fallback"] = "hh_employer"
            yield item.model_copy(update={"metadata": metadata})

    @property
    def __name__(self) -> str:
        return "T2CareerParser"


register_site_parser(
    "t2_career",
    domain_pattern=T2CareerParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:t2.ru",
        has_stable_id=True,
        has_stable_url=True,
        rationale="careers.t2.ru is a landing page; public vacancies live on HH employer 4219.",
    ),
)(T2CareerParser)
