"""Site-specific parser for dynamitejobs.com.

DynamiteJobs uses Algolia for their job board. We hit their public Algolia search API directly.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

logger = structlog.get_logger(__name__)


def _item_from_dict(job: dict[str, Any]) -> RawItem | None:
    title = job.get("title")
    if not title:
        return None

    job_id = job.get("id") or job.get("objectID")
    if not job_id:
        return None

    # URL constructing for DynamiteJobs
    slug = job.get("slug")
    if slug:
        company_slug = job.get("company", {}).get("username", "company")
        job_url = f"https://dynamitejobs.com/company/{company_slug}/remote-job/{slug}"
    else:
        job_url = job.get("applyLink", "")
        if not job_url:
            return None

    # Build HTML text representation
    html_parts = []
    html_parts.append(f"<h1>{title}</h1>")

    company_name = job.get("company", {}).get("name", "")
    if company_name:
        html_parts.append(f"<p><strong>Company:</strong> {company_name}</p>")

    html_parts.append("<p><strong>Details:</strong></p>")
    html_parts.append(f"<pre>{json.dumps(job, ensure_ascii=False, indent=2)}</pre>")

    return build_raw_item(
        source_kind=SourceKind.CAREER_SITE,
        source_name="dynamitejobs.com",
        url=job_url,
        text="\n".join(html_parts),
        external_id=str(job_id),
        metadata={"raw_api_payload": job},
    )


class DynamiteJobsParser:
    domain_pattern = r"^https?://(?:www\.)?dynamitejobs\.com/"
    has_custom_parse = True
    supports_discover = False

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults | None:
        return SiteRuntimeDefaults(render=False)

    def parser_kind(self, url: str) -> str | None:
        return "dynamitejobs"

    async def parse(
        self,
        spec: CareerSiteSpec,
        client: Any,
    ) -> AsyncIterator[RawItem]:
        logger.info("fetching dynamitejobs.com Algolia API", url=spec.url)
        api_url = "https://49hkl9g3sb-dsn.algolia.net/1/indexes/prod_jobs/query"

        headers = {
            "x-algolia-api-key": "578864e6f2d8bc38a05a8f3302d5a9ac",  # gitleaks:allow
            "x-algolia-application-id": "49HKL9G3SB",
            "content-type": "application/x-www-form-urlencoded",
        }

        # We can paginate or just request a large chunk.
        # hitsPerPage=50 is standard, we can request up to spec.limit if it exists.
        limit = spec.limit if spec.limit and spec.limit <= 1000 else 50
        data = {"params": f"query=&hitsPerPage={limit}"}

        resp = await client.post(api_url, headers=headers, json=data)
        resp.raise_for_status()

        data_json = resp.json()
        jobs = data_json.get("hits", [])

        if not jobs:
            logger.warning("dynamitejobs.com: no jobs found in API response")
            return

        emitted = 0
        for job in jobs:
            item = _item_from_dict(job)
            if item:
                emitted += 1
                yield item
                if spec.limit and emitted >= spec.limit:
                    break


register_site_parser(
    "dynamitejobs",
    domain_pattern=DynamiteJobsParser.domain_pattern,
    assessment_hint=known_board_assessment_hint("known_site", "site_parser:dynamitejobs.com"),
)(DynamiteJobsParser)
