"""Site-specific parser for workx.am.

It is an Inertia.js SPA, so we extract the JSON payload from the data-page attribute.
"""

from __future__ import annotations

import html
import json
import re
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


class WorkxAmParser:
    domain_pattern = r"^https?://(?:www\.)?workx\.am/"
    has_custom_parse = True
    supports_discover = False

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults | None:
        return SiteRuntimeDefaults(render=False)

    def parser_kind(self, url: str) -> str | None:
        return "workx_am"

    async def parse(
        self,
        spec: CareerSiteSpec,
        client: Any,
    ) -> AsyncIterator[RawItem]:
        logger.info("fetching workx.am via inertia.js payload", url=spec.url)
        resp = await client.get("https://workx.am/jobs")
        resp.raise_for_status()

        match = re.search(r'<div id="app" data-page="([^"]+)"', resp.text)
        if not match:
            logger.warning("workx.am: no inertia data-page payload found")
            return

        data = json.loads(html.unescape(match.group(1)))
        jobs = data.get("props", {}).get("jobs", [])
        if not isinstance(jobs, list):
            jobs = []
        if not jobs:
            logger.warning("workx.am: no jobs found in payload")
            return

        emitted = 0
        for job in jobs:
            slug = job.get("slug")
            if not slug:
                continue

            job_url = f"https://workx.am/jobs/{slug}"
            title = job.get("title", "")
            company_name = job.get("company", {}).get("name", "")

            html_parts = [f"<h1>{title}</h1>"]
            if company_name:
                html_parts.append(f"<p><strong>Company:</strong> {company_name}</p>")
            html_parts.append(f"<pre>{json.dumps(job, ensure_ascii=False, indent=2)}</pre>")

            item = build_raw_item(
                source_kind=SourceKind.CAREER_SITE,
                source_name="workx.am",
                url=job_url,
                text="\n".join(html_parts),
                external_id=slug,
                metadata={"raw_api_payload": job},
            )
            emitted += 1
            yield item

            if spec.limit and emitted >= spec.limit:
                break


register_site_parser(
    "workx_am",
    domain_pattern=WorkxAmParser.domain_pattern,
    assessment_hint=known_board_assessment_hint("known_site", "site_parser:workx.am"),
)(WorkxAmParser)
