"""Discover-only parser for indeed.com listing URLs."""

from __future__ import annotations

import re
from html import unescape
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

import structlog

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.infrastructure.sources.browser_utils import navigate, open_page
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    browser_scroll_collect_urls,
    resolve_browser_config,
    safe_fetch,
)

logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from job_ftch.domain.source_spec import CareerSiteSpec

_JK_RE = re.compile(r"[?&]jk=([a-f0-9]{8,})", re.IGNORECASE)


def _extract_job_keys(html: str, job_key_re: re.Pattern[str], *, limit: int) -> list[str]:
    seen: set[str] = set()
    keys: list[str] = []
    for job_key in job_key_re.findall(unescape(html)):
        if job_key in seen:
            continue
        seen.add(job_key)
        keys.append(job_key)
        if len(keys) >= limit:
            break
    return keys


def _extract_detail_urls(
    html: str,
    base_url: str,
    *,
    limit: int,
    job_key_re: re.Pattern[str],
) -> list[str]:
    return [
        urljoin(base_url, f"/viewjob?jk={job_key}")
        for job_key in _extract_job_keys(html, job_key_re, limit=limit)
    ]


class IndeedParser:
    domain_pattern = r"^https?://(?:www\.)?indeed\.com(?:/|$)"
    has_custom_parse = True
    supports_discover = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=True,
            wait="domcontentloaded",
            include_if_detail_page=True,
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    def _manifest_value(self, key: str, default: object) -> object:
        manifest_entry = getattr(self, "_manifest_entry", None)
        value = getattr(manifest_entry, key, None) if manifest_entry is not None else None
        return default if value is None else value

    def _detail_url_re(self) -> re.Pattern[str]:
        pattern = str(self._manifest_value("detail_pattern", r"/viewjob\?jk=[a-f0-9]{8,}"))
        return re.compile(pattern, re.IGNORECASE)

    def _job_key_re(self) -> re.Pattern[str]:
        manifest_entry = getattr(self, "_manifest_entry", None)
        extra = getattr(manifest_entry, "extra", {}) if manifest_entry is not None else {}
        pattern = extra.get("job_key_pattern") or _JK_RE.pattern
        return re.compile(str(pattern), re.IGNORECASE)

    def _limit(self, spec_limit: int | None) -> int:
        if spec_limit is not None:
            return spec_limit
        value = self._manifest_value("limit", 50)
        return value if isinstance(value, int) else 50

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        limit = self._limit(spec.limit)
        detail_url_re = self._detail_url_re()
        job_key_re = self._job_key_re()
        try:
            response = await safe_fetch(client, spec.url)
            current_url = str(response.url)
            if detail_url_re.search(current_url):
                return [current_url]
            urls = _extract_detail_urls(
                str(response.text),
                current_url,
                limit=limit,
                job_key_re=job_key_re,
            )
            if urls:
                return urls
        except Exception as exc:
            logger.debug(
                "indeed.http_discover_failed_escalating_to_browser", url=spec.url, error=str(exc)
            )

        bypass_strategy = spec.monitor_config.get("_bypass_strategy")
        browser_config = resolve_browser_config(spec, bypass_strategy)
        async with open_page(browser_config, bypass_strategy=bypass_strategy) as page:
            await navigate(page, spec.url, browser_config)
            current_url = getattr(page, "url", spec.url) or spec.url
            if detail_url_re.search(current_url):
                return [current_url]
            browser = getattr(getattr(self, "_manifest_entry", None), "browser", None)
            return await browser_scroll_collect_urls(
                page,
                current_url,
                detail_url_re,
                limit=limit,
                scroll_loops=getattr(browser, "scroll_loops", None) or 5,
                pause_sec=(getattr(browser, "scroll_pause_ms", None) or 500) / 1000.0,
            )

    @property
    def __name__(self) -> str:
        return "IndeedParser"


register_site_parser(
    "indeed",
    domain_pattern=IndeedParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:indeed.com",
        has_stable_url=True,
        supports_ordered_head=True,
        can_detect_freshness_without_snapshot=False,
        ordered_by_newest=True,
        requires_full_snapshot=False,
        rationale="indeed.com blocks plain HTTP with Security Check, but listing and /viewjob detail pages are retrievable from inside the browser context.",
    ),
)(IndeedParser)
