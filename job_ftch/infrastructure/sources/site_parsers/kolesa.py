"""Discover-only parser for kolesa.group/career/job."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

import structlog
from selectolax.parser import HTMLParser

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


def _extract_detail_urls(
    html: str,
    base_url: str,
    *,
    limit: int,
    detail_re: re.Pattern[str],
) -> list[str]:
    tree = HTMLParser(html)
    seen: set[str] = set()
    urls: list[str] = []
    for anchor in tree.css("a[href]"):
        href = anchor.attributes.get("href")
        if not href:
            continue
        absolute = urljoin(base_url, href.split("?", 1)[0])
        if not detail_re.fullmatch(urlparse(absolute).path):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        urls.append(absolute)
        if len(urls) >= limit:
            break
    return urls


def _nuxt_vacancies_are_explicitly_empty(html: str) -> bool:
    tree = HTMLParser(html)
    node = tree.css_first("#__NUXT_DATA__")
    if node is None:
        return False
    try:
        payload = json.loads(node.text())
    except (TypeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, list):
        return False
    values: list[object] = []
    for item in payload:
        if not isinstance(item, dict) or "vacancy-list" not in item:
            continue
        value = item["vacancy-list"]
        if isinstance(value, int) and 0 <= value < len(payload):
            value = payload[value]
        values.append(value)
    return bool(values) and all(value in (None, [], {}) for value in values)


class KolesaCareerParser:
    domain_pattern = r"^https?://kolesa\.group/career/job(?:[/?#]|$)"
    has_custom_parse = True
    supports_discover = True
    confirmed_empty_on_empty = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(include_if_detail_page=True)

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    def _detail_re(self) -> re.Pattern[str]:
        manifest_entry = getattr(self, "_manifest_entry", None)
        pattern = (
            getattr(manifest_entry, "detail_pattern", None) or r"/career/job/[a-z0-9-]+-\d+(?:/|$)"
        )
        return re.compile(str(pattern), re.IGNORECASE)

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        detail_re = self._detail_re()
        limit = spec.limit or getattr(getattr(self, "_manifest_entry", None), "limit", None) or 50
        try:
            response = await safe_fetch(client, spec.url)
            current_url = str(response.url)
            if detail_re.fullmatch(urlparse(current_url).path):
                return [current_url.split("?", 1)[0]]
            urls = _extract_detail_urls(
                str(response.text),
                current_url,
                limit=limit,
                detail_re=detail_re,
            )
            if urls:
                return urls
            if _nuxt_vacancies_are_explicitly_empty(str(response.text)):
                return []
        except Exception as exc:
            logger.debug(
                "kolesa.http_discover_failed_escalating_to_browser", url=spec.url, error=str(exc)
            )

        bypass_strategy = spec.monitor_config.get("_bypass_strategy")
        browser_config = resolve_browser_config(spec, bypass_strategy)
        async with open_page(browser_config, bypass_strategy=bypass_strategy) as page:
            await navigate(page, spec.url, browser_config)
            current_url = getattr(page, "url", spec.url) or spec.url
            if detail_re.fullmatch(urlparse(current_url).path):
                return [current_url.split("?", 1)[0]]
            browser = getattr(getattr(self, "_manifest_entry", None), "browser", None)
            urls = await browser_scroll_collect_urls(
                page,
                current_url,
                detail_re,
                limit=limit,
                scroll_loops=getattr(browser, "scroll_loops", None) or 5,
                pause_sec=(getattr(browser, "scroll_pause_ms", None) or 500) / 1000.0,
            )
            if not urls:
                raise RuntimeError("kolesa listing did not expose vacancy state")
            return urls

    @property
    def __name__(self) -> str:
        return "KolesaCareerParser"


register_site_parser(
    "kolesa_career",
    domain_pattern=KolesaCareerParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:kolesa.group",
        has_stable_url=True,
        supports_ordered_head=False,
        can_detect_freshness_without_snapshot=False,
        ordered_by_newest=False,
        requires_full_snapshot=False,
        rationale="kolesa.group exposes canonical /career/job/<slug-id> detail URLs in SSR HTML, so a dedicated parser can avoid generic listing/detail misses.",
    ),
)(KolesaCareerParser)
