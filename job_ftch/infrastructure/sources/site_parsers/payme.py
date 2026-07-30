"""Payme career site parser."""

from __future__ import annotations

from typing import TYPE_CHECKING

from job_ftch.application.registry import register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import httpx

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec
    from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults


@register_site_parser("payme", domain_pattern=r"career\.payme\.uz")
class PaymeParser:
    """Parser for career.payme.uz."""

    domain_pattern = "career.payme.uz"
    has_custom_parse = True
    supports_discover = False

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults | None:
        """Payme API is extremely fast, no special runtime defaults needed."""
        return None

    def parser_kind(self, url: str) -> str | None:
        return "payme"

    async def parse(
        self, spec: CareerSiteSpec, client: httpx.AsyncClient
    ) -> AsyncIterator[RawItem]:
        """Fetch jobs directly from the Payme API."""
        resp = await client.get("https://career.payme.uz/api/vacancies")
        resp.raise_for_status()

        jobs = resp.json()
        if not isinstance(jobs, list):
            return

        for job in jobs:
            # We assume it returns dicts. If it's empty, this loop just won't run.
            url = f"https://career.payme.uz/vacancies/{job.get('_id') or job.get('id', '')}"
            yield build_raw_item(
                source_kind=SourceKind.CAREER_SITE,
                source_name=spec.source_name or "payme",
                external_id=str(job.get("_id") or job.get("id") or ""),
                text=str(job),
                url=url,
                metadata={
                    "title": str(job.get("title") or ""),
                    "source_domain": "career.payme.uz",
                    "source_url": str(spec.url),
                },
            )
