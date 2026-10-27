"""VK Team vacancies discovery through its public paginated API."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    normalize_search_keywords,
    safe_fetch,
    text_matches_keywords,
    with_query_params,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


class VkTeamParser:
    domain_pattern = r"^https?://team\.vk\.company/vacancy/"
    has_custom_parse = True
    supports_discover = True
    supports_search = True
    search_mode = "combined"

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
        # Live search box writes ``search=``, which the SPA forwards as API
        # ``title=``. ``query=`` is ignored by the page (unfiltered dump).
        return [with_query_params(base_url, {"search": " OR ".join(terms)})]

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            url_filter=r"team\.vk\.company/vacancy/\d+/?$",
            include_if_detail_page=False,
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        parsed = urlsplit(spec.url)
        query = parse_qs(parsed.query)
        search = next(
            (
                values[0].strip()
                for key in ("query", "search", "title")
                if (values := query.get(key)) and values[0].strip()
            ),
            "",
        )
        terms = normalize_search_keywords(re.split(r"\s+OR\s+|\s+or\s+", search)) if search else []
        # ``title`` is a substring filter, not a boolean OR. Combined role
        # phrases therefore have to be applied locally after listing pages.
        api_title = terms[0] if len(terms) == 1 else ""
        limit = min(spec.limit or 50, 50)
        urls: list[str] = []
        offset = 0
        page_size = 50
        while len(urls) < limit and offset < 200:
            params: dict[str, str | int] = {"limit": page_size, "offset": offset}
            if api_title:
                params["title"] = api_title
            api_url = urljoin(spec.url, "/career/api/v2/vacancies/") + "?" + urlencode(params)
            try:
                response = await safe_fetch(client, api_url)
            except Exception:  # noqa: BLE001 - a partial page is still usable
                break
            payload = json.loads(str(response.text))
            results = payload.get("results", []) if isinstance(payload, dict) else []
            if not isinstance(results, list) or not results:
                break
            for item in results:
                if not isinstance(item, dict):
                    continue
                vacancy_id = item.get("id")
                if vacancy_id is None:
                    continue
                title = str(item.get("title") or item.get("name") or "")
                if terms and title and not text_matches_keywords(title, terms):
                    continue
                urls.append(urljoin(spec.url, f"/vacancy/{vacancy_id}/"))
                if len(urls) >= limit:
                    break
            if api_title:
                break
            offset += len(results)
        return urls

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        for url in await self.discover(spec, client):
            try:
                response = await safe_fetch(client, url)
            except Exception:  # noqa: BLE001 - let the generic path recover on empty
                continue
            article = LexborHTMLParser(str(response.text)).css_first(".article")
            text = " ".join(article.text(separator=" ", strip=True).split()) if article else ""
            if not text:
                continue
            vacancy_id = url.rstrip("/").rsplit("/", 1)[-1]
            yield build_raw_item(
                source_kind=SourceKind.CAREER_SITE,
                source_name=spec.source_name or "vk_team",
                external_id=vacancy_id,
                url=url,
                text=text,
                metadata={
                    "board_url": spec.url,
                    "parser": "vk_team",
                    "company": "VK",
                    "company_authoritative": True,
                    "detail_vacancy_confirmed": True,
                },
            )

    @property
    def __name__(self) -> str:
        return "VkTeamParser"


register_site_parser(
    "vk_team",
    domain_pattern=VkTeamParser.domain_pattern,
    assessment_hint=known_board_assessment_hint("known_site", "site_parser:team.vk.company"),
)(VkTeamParser)
