"""Site-specific parser for arc.dev.

Arc.dev is a Next.js SPA. The job listings are available inside the
__NEXT_DATA__ script on the /remote-jobs page. We extract them directly to avoid
needing to scrape detail pages which might be protected.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.embedded_state_utils import extract_next_data, resolve_path
from job_ftch.infrastructure.sources.monitors.nextdata import _find_jobs_path
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import safe_fetch

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

logger = structlog.get_logger(__name__)


def _item_from_dict(job: dict[str, Any], base_url: str) -> RawItem | None:
    title = job.get("title")
    if not title:
        return None

    # arc.dev typically has job paths like /remote-jobs/uuid-slug
    url_string = job.get("urlString")
    if url_string:
        job_url = url_string
        if not job_url.startswith("http"):
            job_url = f"https://arc.dev/{job_url.lstrip('/')}"
    else:
        # Fallback to base url if no specific url
        job_url = base_url

    # Create html representation of the job
    html_parts = []
    html_parts.append(f"<h1>{title}</h1>")
    company = job.get("company", {})
    company_name = company.get("name", "") if isinstance(company, dict) else str(company)
    if company_name:
        html_parts.append(f"<p><strong>Company:</strong> {company_name}</p>")

    html_parts.append("<p><strong>Details:</strong></p>")
    html_parts.append(f"<pre>{json.dumps(job, ensure_ascii=False, indent=2)}</pre>")

    text_content = "\n".join(html_parts)

    return build_raw_item(
        source_kind=SourceKind.CAREER_SITE,
        source_name="arc.dev",
        url=job_url,
        text=text_content,
        external_id=str(job.get("id") or job.get("slug") or "") or None,
        metadata={"raw_api_payload": job},
    )


class ArcParser:
    domain_pattern = r"^https?://(?:www\.)?arc\.dev/"
    has_custom_parse = True
    supports_discover = False

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults | None:
        return SiteRuntimeDefaults(render=False)

    def parser_kind(self, url: str) -> str | None:
        return "arc"

    async def parse(
        self,
        spec: CareerSiteSpec,
        client: Any,
    ) -> AsyncIterator[RawItem]:
        logger.info("fetching arc.dev next_data", url=spec.url)
        resp = await safe_fetch(client, spec.url)
        html = str(resp.text)

        data = extract_next_data(html)
        if not data:
            logger.warning("arc.dev: no __NEXT_DATA__ found")
            return

        found = _find_jobs_path(data)
        if not found:
            logger.warning("arc.dev: no jobs array found in NEXT_DATA")
            return

        path, _ = found
        jobs = resolve_path(data, path)
        if not jobs or not isinstance(jobs, list):
            return

        emitted = 0
        for job in jobs:
            if not isinstance(job, dict):
                continue
            item = _item_from_dict(job, str(resp.url))
            if item:
                emitted += 1
                yield item
                if spec.limit and emitted >= spec.limit:
                    break


register_site_parser(
    "arc",
    domain_pattern=ArcParser.domain_pattern,
    assessment_hint=known_board_assessment_hint("known_site", "site_parser:arc.dev"),
)(ArcParser)
