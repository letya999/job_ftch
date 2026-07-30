"""Site-specific parser for remoteok.com."""

from __future__ import annotations

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


class RemoteOkParser:
    domain_pattern = r"^https?://(?:www\.)?remoteok\.com/"
    has_custom_parse = True
    supports_discover = False

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults | None:
        return SiteRuntimeDefaults(render=False)

    def parser_kind(self, url: str) -> str | None:
        return "remoteok"

    async def parse(
        self,
        spec: CareerSiteSpec,
        client: Any,
    ) -> AsyncIterator[RawItem]:
        logger.info("fetching remoteok.com via API", url=spec.url)
        resp = await client.get("https://remoteok.com/api")
        resp.raise_for_status()

        jobs = resp.json()
        if not jobs:
            logger.warning("remoteok.com: no jobs found in API response")
            return

        emitted = 0
        for job in jobs:
            if "id" not in job:
                continue

            job_url = job.get("url")
            if not job_url:
                continue

            title = job.get("position", "")
            company = job.get("company", "")
            description = job.get("description", "")

            html_parts = [f"<h1>{title}</h1>"]
            if company:
                html_parts.append(f"<p><strong>Company:</strong> {company}</p>")
            if description:
                html_parts.append(f"<div>{description}</div>")

            item = build_raw_item(
                source_kind=SourceKind.CAREER_SITE,
                source_name="remoteok.com",
                url=job_url,
                text="\n".join(html_parts),
                external_id=str(job["id"]),
                metadata={"raw_api_payload": job},
            )
            emitted += 1
            yield item

            if spec.limit and emitted >= spec.limit:
                break


register_site_parser(
    "remoteok",
    domain_pattern=RemoteOkParser.domain_pattern,
    assessment_hint=known_board_assessment_hint("known_site", "site_parser:remoteok.com"),
)(RemoteOkParser)
