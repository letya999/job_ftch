"""Parsers for Beeline career surfaces."""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlparse, urlunparse

from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    keywords_from_spec,
    normalize_search_keywords,
)
from job_ftch.infrastructure.sources.site_parsers.hh import HhParser

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


class BeelineUzParser:
    """Read vacancies from the API used by Beeline's client-rendered board."""

    domain_pattern = r"^https?://(?:www\.)?beeline\.uz/(?:[a-z]{2}/)?vacancies/?(?:[?#].*)?$"
    has_custom_parse = True
    _API_URL = "https://beeline.uz/msapi/web/vacancies/"

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(render=False, include_if_detail_page=False)

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    @staticmethod
    def _plain_html(value: object) -> str:
        if not isinstance(value, str) or not value:
            return ""
        return " ".join(LexborHTMLParser(value).text(separator=" ", strip=True).split())

    @classmethod
    def _item_from_payload(cls, vacancy: dict[str, Any], spec: CareerSiteSpec) -> RawItem | None:
        vacancy_id = vacancy.get("id")
        title = cls._plain_html(vacancy.get("name"))
        slug = vacancy.get("slug")
        if not (title and isinstance(vacancy_id, int) and isinstance(slug, str) and slug):
            return None

        description = vacancy.get("description")
        detail_sections = description if isinstance(description, dict) else {}
        text_parts = [title, cls._plain_html(vacancy.get("content"))]
        text_parts.extend(
            cls._plain_html(detail_sections.get(key))
            for key in ("responsibilities", "requirements", "conditions")
        )
        text = "\n".join(part for part in text_parts if part)
        if not text:
            return None

        region = detail_sections.get("regionTitle")
        if not isinstance(region, str):
            region_data = vacancy.get("region")
            region = region_data.get("name") if isinstance(region_data, dict) else None
        created_at: datetime | None = None
        created_at_value = vacancy.get("created_at")
        if isinstance(created_at_value, str):
            with suppress(ValueError):
                created_at = datetime.fromisoformat(created_at_value)
        return build_raw_item(
            source_kind=SourceKind.CAREER_SITE,
            source_name=spec.source_name or "beeline_uz",
            external_id=str(vacancy_id),
            url=f"https://beeline.uz/ru/vacancies/{quote(slug, safe='-')}",
            text=text,
            created_at=created_at,
            metadata={
                "title": title,
                "location": region,
                "board_url": spec.url,
                "parser": "beeline_uz",
                "detail_vacancy_confirmed": True,
            },
        )

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        limit = spec.limit or 50
        emitted = 0
        page = 1
        while emitted < limit:
            response = await client.get(
                self._API_URL,
                params={"page": page, "per_page": min(limit, 100), "locale": "ru"},
                follow_redirects=True,
            )
            response.raise_for_status()
            payload = response.json()
            vacancies = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(vacancies, list) or not vacancies:
                return
            for vacancy in vacancies:
                if not isinstance(vacancy, dict):
                    continue
                item = self._item_from_payload(vacancy, spec)
                if item is None:
                    continue
                yield item
                emitted += 1
                if emitted >= limit:
                    return
            pagination = payload.get("pagination") if isinstance(payload, dict) else None
            pages = pagination.get("pages") if isinstance(pagination, dict) else None
            if not isinstance(pages, int) or page >= pages:
                return
            page += 1


register_site_parser("beeline_uz", domain_pattern=BeelineUzParser.domain_pattern)(BeelineUzParser)


_BEELINE_RU_HH_EMPLOYER_ID = "4934"
_BEELINE_RU_HH_URL = f"https://hh.ru/search/vacancy?employer_id={_BEELINE_RU_HH_EMPLOYER_ID}"


class BeelineRuParser:
    """Public Beeline RU hiring is geo-blocked; HH employer 4934 is the listing."""

    domain_pattern = r"^https?://(?:www\.)?jobs?\.beeline\.ru(?:/|$)"
    has_custom_parse = True
    supports_search = True
    search_mode = "combined"
    confirmed_empty_on_empty = True

    def build_search_urls(
        self, base_url: str, keywords: Any, *, limit: int | None = None
    ) -> list[str]:
        del limit
        if not normalize_search_keywords(keywords):
            return []
        # Keep the Beeline host so this parser stays selected. HH is queried
        # inside ``parse``; rewriting to hh.ru would dispatch HhParser and
        # lose ``confirmed_empty_on_empty``.
        parsed = urlparse(base_url)
        listing = urlunparse(parsed._replace(query="", fragment=""))
        return [listing or "https://job.beeline.ru/vacancies"]

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=False,
            wait="domcontentloaded",
            include_if_detail_page=False,
            extra={
                "proxy_rescue_allow_domains": [
                    "hh.ru",
                    "job.beeline.ru",
                    "jobs.beeline.ru",
                ],
            },
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return "beeline_ru_hh"

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        keywords = keywords_from_spec(spec)
        urls = self.build_search_urls(_BEELINE_RU_HH_URL, keywords, limit=spec.limit)
        delegated = spec.model_copy(update={"url": urls[0] if urls else _BEELINE_RU_HH_URL})
        async for item in HhParser().parse(delegated, client):
            metadata = dict(item.metadata or {})
            metadata["parser"] = "beeline_ru_hh"
            metadata.setdefault("company", "Билайн")
            metadata["company_authoritative"] = True
            metadata["fallback"] = "hh_employer"
            yield item.model_copy(update={"metadata": metadata})


register_site_parser("beeline_ru", domain_pattern=BeelineRuParser.domain_pattern)(BeelineRuParser)
