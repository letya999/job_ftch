"""Site parser for EPAM careers.

EPAM's public careers UI hydrates from a stable JSON endpoint.  Prefer that
endpoint over scraping the rendered app: several public entrypoints contain
WAF/vendor scripts, while ``/en/jobs`` and the API are directly readable.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import effective_limit
from job_ftch.infrastructure.sources.source_deadline import await_with_source_deadline

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


_DOMAIN_PATTERN = r"^https?://(?:careers\.epam\.com|www\.epam\.com/careers)(?:/|$)"
_API_URL = (
    "https://careers.epam.com/api/jobs/v2/search/careers-i18n"
    "?from=0&lang=en&size={size}&sortBy=relevance%3Brelocation%3Dasc&websiteLocale=en-us"
)


def _strip_html(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    return " ".join(text.split())


def _names(values: Any) -> str:
    if not isinstance(values, list):
        return ""
    names = []
    for value in values:
        if isinstance(value, dict) and value.get("name"):
            names.append(str(value["name"]))
    return ", ".join(names)


class EpamCareerParser:
    domain_pattern = _DOMAIN_PATTERN
    has_custom_parse = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=True,
            wait="domcontentloaded",
            include_if_detail_page=False,
            extra={"canonical_url": "https://careers.epam.com/en/jobs"},
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        from job_ftch.config import get_settings

        limit = max(1, effective_limit(spec, get_settings()))
        response = await await_with_source_deadline(
            client.get(
                _API_URL.format(size=min(limit, 100)),
                follow_redirects=True,
                headers={"Accept": "application/json"},
            )
        )
        data = response.json()
        jobs = data.get("data", {}).get("jobs", []) if isinstance(data, dict) else []
        source_name = spec.source_name or "epam_careers"
        for job in jobs[:limit]:
            if not isinstance(job, dict):
                continue
            title = str(job.get("name") or "").strip()
            uid = str(job.get("uid") or job.get("_key") or title).strip()
            if not title or not uid:
                continue
            country = _names(job.get("country"))
            city = _names(job.get("city"))
            skills = ", ".join(str(skill) for skill in (job.get("skills") or []) if skill)
            description = _strip_html(job.get("description"))
            job_url = f"https://careers.epam.com/en/vacancy/{job.get('_key') or uid}"
            text_parts = [
                title,
                f"Location: {city or country}" if (city or country) else "",
                f"Seniority: {job.get('seniority')}" if job.get("seniority") else "",
                f"Workplace: {job.get('vacancy_type')}" if job.get("vacancy_type") else "",
                f"Skills: {skills}" if skills else "",
                description,
            ]
            yield build_raw_item(
                source_kind=SourceKind.CAREER_SITE,
                source_name=source_name,
                external_id=uid,
                url=job_url,
                text="\n".join(part for part in text_parts if part),
                metadata={
                    "board_url": "https://careers.epam.com/en/jobs",
                    "job_url": job_url,
                    "location": city or country,
                    "parser": "epam_api",
                    "adapter": "epam_api",
                },
            )


register_site_parser(
    "epam",
    domain_pattern=EpamCareerParser.domain_pattern,
    assessment_hint=known_board_assessment_hint("known_site", "site_parser:epam_api"),
)(EpamCareerParser)
