"""Site parsers for Ozon career surfaces.

``career.ozon.ru`` / ``job.ozon.ru`` use the public vacancy API. The
``ozon.tech`` board is still a rendered SPA and only supplies runtime defaults.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    DEFAULT_LISTING_MAX_PAGES,
    distinctive_search_tokens,
    keywords_from_spec,
    normalize_search_keywords,
    text_matches_keywords,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

_URL_FILTER = r"ozon\.tech/vacancies/[a-f0-9\-]+-[a-z0-9-]+/?$"
_OZON_JOB_PATTERN = r"^https?://(?:job|career)\.ozon\.ru(?:/|$)"
_OZON_VACANCY_URL = "https://career.ozon.ru/vacancy/"


class OzonTechParser:
    domain_pattern = r"^https?://ozon\.tech/vacancies"
    has_custom_parse = False  # runtime-defaults only

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            url_filter=_URL_FILTER,
            render=True,
            wait="domcontentloaded",
            include_if_detail_page=False,
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def parse(
        self,
        spec: CareerSiteSpec,
        client: Any,
    ) -> AsyncIterator[RawItem]:
        del spec, client
        return
        yield  # pragma: no cover

    @property
    def __name__(self) -> str:
        return "OzonTechParser"


register_site_parser(
    "ozon_tech",
    domain_pattern=OzonTechParser.domain_pattern,
    assessment_hint=known_board_assessment_hint("known_site", "site_parser:ozon.tech"),
)(OzonTechParser)


class OzonCareerParser:
    domain_pattern = _OZON_JOB_PATTERN
    has_custom_parse = True
    supports_search = True
    search_mode = "combined"
    confirmed_empty_on_empty = True

    def build_search_urls(
        self,
        base_url: str,
        keywords: Any,
        *,
        limit: int | None = None,
    ) -> list[str]:
        del base_url, limit
        if not normalize_search_keywords(keywords):
            return []
        # Full role phrases in ``query=`` return zero live API rows. Search
        # tokens are applied inside ``parse`` from the profile roles.
        return [_OZON_VACANCY_URL]

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=False,
            wait="domcontentloaded",
            include_if_detail_page=False,
            extra={"canonical_url": _OZON_VACANCY_URL},
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        limit = spec.limit or 50
        keywords = keywords_from_spec(spec)
        queries: list[str | None] = [*distinctive_search_tokens(keywords)] or [None]
        emitted = 0
        seen: set[str] = set()
        page_size = min(50, max(limit, 1))
        for query in queries:
            for page in range(1, DEFAULT_LISTING_MAX_PAGES + 1):
                params: dict[str, Any] = {"meta.limit": page_size, "meta.page": page}
                if query:
                    params["query"] = query
                response = await client.get(
                    "https://job-api.ozon.ru/v2/vacancy",
                    params=params,
                    follow_redirects=True,
                )
                response.raise_for_status()
                payload = response.json()
                items = payload.get("items", []) if isinstance(payload, dict) else []
                if not isinstance(items, list) or not items:
                    break
                for vacancy in items:
                    if not isinstance(vacancy, dict):
                        continue
                    external_id = str(vacancy.get("internalUuid") or vacancy.get("hhId") or "")
                    title = str(vacancy.get("title") or "").strip()
                    if not external_id or not title or external_id in seen:
                        continue
                    seen.add(external_id)
                    roles = vacancy.get("professionalRoles") or []
                    text = "\n".join(
                        filter(
                            None,
                            (
                                title,
                                str(vacancy.get("department") or "").strip(),
                                str(vacancy.get("city") or "").strip(),
                                ", ".join(str(role.get("title") or "") for role in roles),
                                str(vacancy.get("description") or "").strip(),
                            ),
                        )
                    )
                    if not text_matches_keywords(text, keywords):
                        continue
                    yield build_raw_item(
                        source_kind=SourceKind.CAREER_SITE,
                        source_name=spec.source_name or "ozon_api",
                        external_id=external_id,
                        url=f"{_OZON_VACANCY_URL}{external_id}",
                        text=text,
                        metadata={
                            "board_url": spec.url,
                            "parser": "ozon_api",
                            "observation_kind": "vacancy_detail",
                            "detail_vacancy_confirmed": True,
                            "company": "Ozon",
                            "company_authoritative": True,
                        },
                    )
                    emitted += 1
                    if emitted >= limit:
                        return
                if len(items) < page_size:
                    break


register_site_parser(
    "ozon_career",
    domain_pattern=OzonCareerParser.domain_pattern,
    assessment_hint=known_board_assessment_hint("known_site", "site_parser:ozon_career_dom"),
)(OzonCareerParser)
