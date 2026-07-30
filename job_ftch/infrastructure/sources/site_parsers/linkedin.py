"""Site parser for linkedin.com regional jobs pages."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.browser_utils import navigate, open_page
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    browser_scroll_collect_urls,
    resolve_browser_config,
)
from job_ftch.infrastructure.sources.source_policy import source_policy_metadata

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

_DOMAIN_PATTERN = r"^(?:https?://)?(?:(?:[a-z]{2,3}|www)\.)?linkedin\.com(?:/|$)"


def _metadata(url: str, *, title: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {**source_policy_metadata(url), "parser": "linkedin"}
    if title:
        result["title"] = title
    return result


@register_site_parser(
    "linkedin",
    domain_pattern=_DOMAIN_PATTERN,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:linkedin.com",
        has_stable_url=True,
        supports_ordered_head=False,
        can_detect_freshness_without_snapshot=False,
        ordered_by_newest=False,
        requires_full_snapshot=False,
        rationale="LinkedIn jobs pages expose stable /jobs/view/<id> URLs, but listing pages also contain many non-job suggestions and need a dedicated parser path.",
    ),
)
class LinkedinParser:
    """Parser for LinkedIn jobs pages with strict detail-link filtering."""

    domain_pattern = _DOMAIN_PATTERN
    has_custom_parse = True
    terminal_on_empty = True
    supports_discover = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            url_filter=r"linkedin\.com/jobs/view/(?:[^/?#]+-)?\d+",
            include_if_detail_page=True,
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return "linkedin"

    def _detail_re(self) -> re.Pattern[str]:
        manifest_entry = getattr(self, "_manifest_entry", None)
        pattern = (
            getattr(manifest_entry, "detail_pattern", None) or r"/jobs/view/(?:[^/?#]+-)?\d+/?"
        )
        return re.compile(str(pattern), re.IGNORECASE)

    def _limit(self, spec_limit: int | None) -> int:
        return spec_limit or getattr(getattr(self, "_manifest_entry", None), "limit", None) or 50

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        del client
        browser_config = resolve_browser_config(spec, spec.monitor_config.get("_bypass_strategy"))
        async with open_page(
            browser_config,
            bypass_strategy=spec.monitor_config.get("_bypass_strategy"),
        ) as page:
            await navigate(page, spec.url, browser_config)
            return await browser_scroll_collect_urls(
                page,
                str(getattr(page, "url", spec.url) or spec.url),
                self._detail_re(),
                limit=self._limit(spec.limit),
                scroll_loops=2,
                pause_sec=0.5,
            )

    async def parse(
        self,
        spec: CareerSiteSpec,
        client: Any,
    ) -> AsyncIterator[RawItem]:
        response = await client.get(spec.url, follow_redirects=True)
        html = response.text
        current_url = str(response.url)
        limit = self._limit(spec.limit)
        source_name = spec.source_name or "linkedin"
        detail_re = self._detail_re()

        emitted = 0
        for item in self._items_from_embedded_json(html, current_url, source_name):
            yield item
            emitted += 1
            if emitted >= limit:
                return

        if emitted > 0:
            return

        parser = HTMLParser(html)
        seen: set[str] = set()
        for link in parser.css('a[href*="/jobs/view/"]'):
            href = link.attributes.get("href")
            if not href or not detail_re.search(href):
                continue
            full_url = urljoin(current_url, href.split("?", 1)[0])
            if full_url in seen:
                continue
            seen.add(full_url)
            title = link.text(separator=" ", strip=True) or full_url.rsplit("/", 1)[-1]
            yield build_raw_item(
                source_name=source_name,
                source_kind=SourceKind.CAREER_SITE,
                url=full_url,
                external_id=full_url,
                text=title,
                metadata=_metadata(full_url, title=title),
            )
            emitted += 1
            if emitted >= limit:
                return

    def _find_jobs_in_json(self, data: Any) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        if isinstance(data, dict):
            urn = str(data.get("entityUrn", ""))
            if data.get("$type") == "com.linkedin.voyager.jobs.JobPosting" or "jobPosting" in urn:
                jobs.append(data)
            for value in data.values():
                jobs.extend(self._find_jobs_in_json(value))
        elif isinstance(data, list):
            for item in data:
                jobs.extend(self._find_jobs_in_json(item))
        return jobs

    def _items_from_embedded_json(
        self,
        html: str,
        base_url: str,
        source_name: str,
    ) -> list[RawItem]:
        parser = HTMLParser(html)
        items: list[RawItem] = []
        seen: set[str] = set()
        for code_node in parser.css('code[id^="bpr-guid-"]'):
            text_content = code_node.text(strip=True)
            if not text_content or "jobposting" not in text_content.lower():
                continue
            try:
                data = json.loads(text_content)
            except json.JSONDecodeError:
                continue
            for job in self._find_jobs_in_json(data):
                job_url = str(job.get("url") or "").strip()
                if not job_url or not self._detail_re().search(job_url):
                    continue
                full_url = urljoin(base_url, job_url)
                if full_url in seen:
                    continue
                seen.add(full_url)
                title = str(job.get("title") or "").strip() or full_url.rsplit("/", 1)[-1]
                items.append(
                    build_raw_item(
                        source_name=source_name,
                        source_kind=SourceKind.CAREER_SITE,
                        url=full_url,
                        external_id=full_url,
                        text=json.dumps(job, ensure_ascii=False),
                        metadata=_metadata(full_url, title=title),
                    )
                )
        return items
