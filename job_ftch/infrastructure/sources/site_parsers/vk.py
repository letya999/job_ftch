"""VK Team vacancies discovery through its public paginated API."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import safe_fetch

if TYPE_CHECKING:
    from job_ftch.domain.source_spec import CareerSiteSpec


class VkTeamParser:
    domain_pattern = r"^https?://team\.vk\.company/vacancy/"
    has_custom_parse = True
    supports_discover = True

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
        limit = min(spec.limit or 50, 50)
        params: dict[str, str | int] = {"limit": limit, "offset": 0}
        if search:
            params["title"] = search
        api_url = urljoin(spec.url, "/career/api/v2/vacancies/") + "?" + urlencode(params)
        response = await safe_fetch(client, api_url)
        payload = json.loads(str(response.text))
        results = payload.get("results", []) if isinstance(payload, dict) else []
        urls: list[str] = []
        for item in results:
            vacancy_id = item.get("id") if isinstance(item, dict) else None
            if vacancy_id is None:
                continue
            urls.append(urljoin(spec.url, f"/vacancy/{vacancy_id}/"))
            if len(urls) >= limit:
                break
        return urls

    @property
    def __name__(self) -> str:
        return "VkTeamParser"


register_site_parser(
    "vk_team",
    domain_pattern=VkTeamParser.domain_pattern,
    assessment_hint=known_board_assessment_hint("known_site", "site_parser:team.vk.company"),
)(VkTeamParser)
