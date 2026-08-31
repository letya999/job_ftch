"""Parser for the official Trudvsem open-data vacancy API."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    normalize_search_keywords,
    safe_fetch,
    with_query_params,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

_API_URL = "https://opendata.trudvsem.ru/api/v1/vacancies"
_SITE_URL = "https://trudvsem.ru/"


def _date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return None


def _text(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(part for part in (_text(item) for item in value) if part)
    if isinstance(value, dict):
        return ", ".join(part for part in (_text(item) for item in value.values()) if part)
    return " ".join(str(value or "").split())


class TrudvsemParser:
    domain_pattern = r"^https?://(?:www\.)?trudvsem\.ru(?:/|$)"
    has_custom_parse = True
    supports_discover = False
    confirmed_empty_on_empty = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=False, wait="domcontentloaded", include_if_detail_page=False
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return "trudvsem_api"

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        limit = spec.limit or getattr(getattr(self, "_manifest_entry", None), "limit", None) or 50
        keywords = normalize_search_keywords(spec.monitor_config.get("_search_keywords"), cap=3)
        params: dict[str, str] = {"limit": str(limit), "offset": "0"}
        if keywords:
            params["text"] = keywords[0]
        response = await safe_fetch(client, with_query_params(_API_URL, params))
        payload = response.json()
        vacancies = (
            ((payload.get("results") or {}).get("vacancies") or [])
            if isinstance(payload, dict)
            else []
        )
        emitted = 0
        for wrapper in vacancies[:limit]:
            vacancy = wrapper.get("vacancy", wrapper) if isinstance(wrapper, dict) else {}
            if not isinstance(vacancy, dict):
                continue
            title = _text(vacancy.get("job-name"))
            external_id = _text(vacancy.get("id"))
            if not title or not external_id:
                continue
            company = vacancy.get("company") or {}
            company = company if isinstance(company, dict) else {}
            region = vacancy.get("region") or {}
            region = region if isinstance(region, dict) else {}
            url = urljoin(
                _SITE_URL,
                _text(vacancy.get("vac_url")) or f"vacancy/card/{external_id}",
            )
            text = "\n".join(
                part
                for part in (
                    title,
                    _text(vacancy.get("duty")),
                    _text(vacancy.get("requirements")),
                    _text(vacancy.get("qualification")),
                    _text(vacancy.get("schedule")),
                    _text(vacancy.get("salary")),
                    _text(company.get("name")),
                    _text(region.get("name")),
                )
                if part
            )
            yield build_raw_item(
                source_kind=SourceKind.CAREER_SITE,
                source_name=spec.source_name or "trudvsem_api",
                external_id=external_id,
                url=url,
                created_at=_date(vacancy.get("creation-date") or vacancy.get("date_modify")),
                text=text,
                metadata={
                    "board_url": spec.url,
                    "parser": "trudvsem_api",
                    "observation_kind": "vacancy_detail",
                    "detail_vacancy_confirmed": True,
                    "company": _text(company.get("name")),
                    "company_authoritative": bool(company.get("name")),
                    "region": _text(region.get("name")),
                },
            )
            emitted += 1
            if emitted >= limit:
                return


register_site_parser(
    "trudvsem",
    domain_pattern=TrudvsemParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:trudvsem_api",
        has_stable_id=True,
        has_stable_url=True,
        has_publication_time=True,
        rationale="Official Trudvsem open-data API returns structured vacancies with stable IDs.",
    ),
)(TrudvsemParser)
