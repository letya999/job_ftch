"""Parser for Joberty public jobs API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


_BOARD_URL = "https://www.joberty.com/it-jobs"
_API_URL = "https://backend.joberty.com/api/v1/jobs"


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _join(values: Any) -> str:
    if not isinstance(values, list):
        return ""
    return ", ".join(_clean(value) for value in values if _clean(value))


def _html_to_text(value: str) -> str:
    if not value:
        return ""
    return " ".join(LexborHTMLParser(value).text(separator=" ", strip=True).split())


class JobertyParser:
    """Read Joberty job cards and descriptions from its public backend API."""

    domain_pattern = r"^https?://(?:www\.)?joberty\.com(?:/|$)"
    has_custom_parse = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=False,
            wait="domcontentloaded",
            include_if_detail_page=False,
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        limit = spec.limit or 50
        response = await client.get(
            _API_URL,
            params={"page": 0, "pageSize": limit},
            headers={"Accept": "application/json", "Referer": _BOARD_URL},
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return

        source_name = spec.source_name or "joberty"
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            job_id = row.get("id")
            title = _clean(row.get("jobTitle"))
            if not job_id or not title:
                continue
            detail = await self._get_detail(client, str(job_id))
            merged = {**row, **detail}
            company_slug = _clean(merged.get("companyUrlName"))
            job_url = (
                f"{_BOARD_URL}/{company_slug}-{job_id}"
                if company_slug
                else f"{_BOARD_URL}/{job_id}"
            )
            company = _clean(merged.get("companyName"))
            cities = _join(merged.get("cities"))
            domains = _join(merged.get("domains"))
            technologies = _join(merged.get("technologies"))
            seniority = _clean(merged.get("seniority"))
            description = _html_to_text(str(merged.get("text") or ""))
            apply_url = _clean(merged.get("applyUrl"))
            yield build_raw_item(
                source_kind=SourceKind.CAREER_SITE,
                source_name=source_name,
                external_id=str(job_id),
                url=job_url,
                text="\n".join(
                    part
                    for part in (
                        title,
                        company,
                        cities,
                        domains,
                        technologies,
                        seniority,
                        description,
                    )
                    if part
                ),
                metadata={
                    "board_url": _BOARD_URL,
                    "job_url": job_url,
                    "apply_url": apply_url or None,
                    "company": company or None,
                    "location": cities or None,
                    "domains": domains or None,
                    "technologies": technologies or None,
                    "seniority": seniority or None,
                    "parser": "joberty_public_jobs_api",
                },
            )

    async def _get_detail(self, client: Any, job_id: str) -> dict[str, Any]:
        response = await client.get(
            f"{_API_URL}/{job_id}",
            headers={"Accept": "application/json", "Referer": _BOARD_URL},
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}


register_site_parser("joberty", domain_pattern=JobertyParser.domain_pattern)(JobertyParser)
