"""Site-specific parser for cord.co.

Cord is a large aggregator using an internal GraphQL/REST API.
We hit their public API directly.
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
    title = job.get("position")
    if not title:
        return None

    # URL constructing for Cord
    job_id = job.get("listingID", "")

    if job_id:
        job_url = f"https://cord.co/jobs/#job-{job_id}"
    else:
        return None

    # Build HTML text representation
    html_parts = []
    html_parts.append(f"<h1>{title}</h1>")

    company_name = job.get("companyName", "")
    if company_name:
        html_parts.append(f"<p><strong>Company:</strong> {company_name}</p>")

    html_parts.append("<p><strong>Details:</strong></p>")
    html_parts.append(f"<pre>{json.dumps(job, ensure_ascii=False, indent=2)}</pre>")

    return build_raw_item(
        source_kind=SourceKind.CAREER_SITE,
        source_name="cord.co",
        url=job_url,
        text="\n".join(html_parts),
        external_id=str(job_id),
        metadata={"raw_api_payload": job},
    )


class CordParser:
    domain_pattern = r"^https?://(?:www\.)?cord\.co/"
    has_custom_parse = True
    supports_discover = False

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults | None:
        return SiteRuntimeDefaults(render=False)

    def parser_kind(self, url: str) -> str | None:
        return "cord"

    async def parse(
        self,
        spec: CareerSiteSpec,
        client: Any,
    ) -> AsyncIterator[RawItem]:
        logger.info("fetching cord.co API", url=spec.url)
        # Using the API endpoint found in network traces
        api_url = "https://cord.com/api/v2/public/search?page=0&passiveSearch=true&view=listing&companyType=all"

        # Cord requires a User-Agent, bypass_httpx usually sets one but just in case
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"
        }

        resp = await client.get(api_url, headers=headers)
        resp.raise_for_status()

        data = resp.json()
        jobs = data.get("data", {}).get("values", [])

        if not jobs:
            logger.warning("cord.co: no jobs found in API response")
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
    "cord",
    domain_pattern=CordParser.domain_pattern,
    assessment_hint=known_board_assessment_hint("known_site", "site_parser:cord.co"),
)(CordParser)
