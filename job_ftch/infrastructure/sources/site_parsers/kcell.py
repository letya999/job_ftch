"""HTTP parser for the Kcell careers SPA JSON API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import with_query_params

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

_API_URL = "https://jobs.kcell.kz/api/jobs"
_JOB_URL = "https://jobs.kcell.kz/job/{job_id}"
_PAGE_SIZE = 50


def _localized(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _nested_name(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    return _localized(payload, "nameRu", "nameEn", "nameKk")


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class KcellParser:
    domain_pattern = r"^https?://jobs\.kcell\.kz(?:/|$)"
    has_custom_parse = True
    confirmed_empty_on_empty = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=False,
            include_if_detail_page=False,
            url_filter=r"jobs\.kcell\.kz/job/\d+",
            extra={
                "captcha_authorized_domains": ["jobs.kcell.kz"],
                "proxy_rescue_allow_domains": ["jobs.kcell.kz"],
            },
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        limit = spec.limit or 50
        source_name = spec.source_name or "kcell"
        emitted = 0
        page = 0
        while emitted < limit:
            listing_url = with_query_params(
                _API_URL,
                {
                    "isPublic": "true",
                    "statusJob": "PUBLISHED",
                    "page": str(page),
                    "size": str(min(_PAGE_SIZE, limit - emitted)),
                },
            )
            listing = await client.get(listing_url, follow_redirects=True)
            listing.raise_for_status()
            payload = listing.json()
            rows = payload.get("content") if isinstance(payload, dict) else payload
            if not isinstance(rows, list) or not rows:
                return
            for vacancy in rows:
                if not isinstance(vacancy, dict):
                    continue
                job_id = vacancy.get("jobId")
                title = _localized(vacancy, "nameRu", "nameEn", "nameKk")
                if job_id is None or not title:
                    continue
                description = _localized(vacancy, "descRu", "descEn", "descKk")
                location = _nested_name(vacancy.get("city"))
                team = _nested_name(vacancy.get("team"))
                job_type = _nested_name(vacancy.get("jobType"))
                parts = [title, team, location, job_type, description]
                yield build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=source_name,
                    external_id=str(job_id),
                    url=_JOB_URL.format(job_id=job_id),
                    text="\n".join(part for part in parts if part),
                    created_at=_parse_datetime(
                        vacancy.get("createdDate") or vacancy.get("updatedDate")
                    ),
                    metadata={
                        "board_url": spec.url,
                        "parser": "kcell_api",
                        "detail_vacancy_confirmed": True,
                        "company": "Kcell",
                        "company_authoritative": True,
                        "location": location or None,
                    },
                )
                emitted += 1
                if emitted >= limit:
                    return
            if isinstance(payload, dict) and payload.get("last") is True:
                return
            page += 1


register_site_parser(
    "kcell",
    domain_pattern=KcellParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:jobs.kcell.kz",
        has_stable_id=True,
        has_publication_time=True,
        requires_full_snapshot=False,
        rationale="jobs.kcell.kz is a SPA whose public listing lives at /api/jobs.",
    ),
)(KcellParser)
