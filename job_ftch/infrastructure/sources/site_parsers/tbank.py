"""Discover-only parser for tbank.ru/career."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

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
from job_ftch.infrastructure.sources.url_scoring import score_job_url

logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from job_ftch.domain.source_spec import CareerSiteSpec


def _extract_detail_urls_from_hrefs(
    hrefs: list[str],
    base_url: str,
    *,
    limit: int,
    detail_re: re.Pattern[str],
    listing_re: re.Pattern[str],
) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for href in hrefs:
        absolute = urljoin(base_url, href.split("?", 1)[0])
        if not detail_re.fullmatch(absolute):
            continue
        if score_job_url(absolute, board_url=base_url) <= 0 or listing_re.search(absolute):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        urls.append(absolute)
        if len(urls) >= limit:
            break
    return urls


def _extract_detail_urls(
    html: str,
    base_url: str,
    *,
    limit: int,
    detail_re: re.Pattern[str],
    listing_re: re.Pattern[str],
) -> list[str]:
    urls = _extract_detail_urls_from_hrefs(
        detail_re.findall(html),
        base_url,
        limit=limit,
        detail_re=detail_re,
        listing_re=listing_re,
    )
    if len(urls) >= limit:
        return urls

    tree = HTMLParser(html)
    hrefs = [str(anchor.attributes.get("href", "") or "") for anchor in tree.css("a[href]")]
    extra = _extract_detail_urls_from_hrefs(
        hrefs,
        base_url,
        limit=limit,
        detail_re=detail_re,
        listing_re=listing_re,
    )
    seen = set(urls)
    for url in extra:
        if url in seen:
            continue
        urls.append(url)
        seen.add(url)
        if len(urls) >= limit:
            break
    return urls


class TbankCareerParser:
    domain_pattern = r"^https?://(?:www\.)?tbank\.ru/career"
    has_custom_parse = True
    supports_discover = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            url_filter=(
                r"tbank\.ru/career/(?:it/)?vacanc(?:y|ies)/"
                r"(?:[a-z0-9_-]+/)+[a-z0-9_-]+/?$"
            ),
            include_if_detail_page=False,
            expand_links=(
                r"tbank\.ru/career/it(?:/|$)",
                r"tbank\.ru/career/it/ml(?:/|$)",
                r"tbank\.ru/career/vacancies/it(?:/|\?|$)",
            ),
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    def _manifest_value(self, key: str, default: object) -> object:
        manifest_entry = getattr(self, "_manifest_entry", None)
        value = getattr(manifest_entry, key, None) if manifest_entry is not None else None
        return default if value is None else value

    def _detail_re(self) -> re.Pattern[str]:
        pattern = str(
            self._manifest_value(
                "detail_pattern",
                r"https?://(?:www\.)?tbank\.ru/career/(?:it/)?vacanc(?:y|ies)/"
                r"(?:[a-z0-9_-]+/)+[a-z0-9_-]+/?$",
            )
        )
        return re.compile(pattern, re.IGNORECASE)

    def _listing_re(self) -> re.Pattern[str]:
        pattern = str(
            self._manifest_value(
                "listing_pattern",
                r"/career/(?:vacancies/(?:all|service|back-office|it)|service/|blog(?:/|$)|technologies(?:/|$))",
            )
        )
        return re.compile(pattern, re.IGNORECASE)

    def _limit(self, spec_limit: int | None) -> int:
        if spec_limit is not None:
            return spec_limit
        raw_limit = self._manifest_value("limit", 50)
        if isinstance(raw_limit, (int, str, float)):
            return int(raw_limit)
        return 50

    def _expand_patterns(self) -> tuple[re.Pattern[str], ...]:
        patterns = self._manifest_value(
            "expand_links",
            (
                r"tbank\.ru/career/it(?:/|$)",
                r"tbank\.ru/career/it/ml(?:/|$)",
                r"tbank\.ru/career/vacancies/it(?:/|\?|$)",
            ),
        )
        if not isinstance(patterns, (list, tuple)):
            return ()
        return tuple(re.compile(str(pattern), re.IGNORECASE) for pattern in patterns)

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        limit = self._limit(spec.limit)
        detail_re = self._detail_re()
        listing_re = self._listing_re()
        expand_patterns = self._expand_patterns()
        collected: list[str] = []
        seen: set[str] = set()

        def _merge(urls: list[str]) -> None:
            for url in urls:
                if url in seen:
                    continue
                seen.add(url)
                collected.append(url)
                if len(collected) >= limit:
                    return

        try:
            response = await safe_fetch(client, spec.url)
            current_url = str(getattr(response, "url", spec.url) or spec.url)
            current_url_candidates = _extract_detail_urls_from_hrefs(
                [current_url],
                current_url,
                limit=1,
                detail_re=detail_re,
                listing_re=listing_re,
            )
            if current_url_candidates:
                return current_url_candidates
            _merge(
                _extract_detail_urls(
                    str(response.text),
                    current_url,
                    limit=limit,
                    detail_re=detail_re,
                    listing_re=listing_re,
                )
            )
            if len(collected) >= limit:
                return collected[:limit]
            tree = HTMLParser(str(response.text))
            expand_urls = []
            for anchor in tree.css("a[href]"):
                href = str(anchor.attributes.get("href", "") or "")
                absolute = urljoin(current_url, href)
                if any(pattern.search(absolute) for pattern in expand_patterns):
                    expand_urls.append(absolute)
            for expand_url in expand_urls:
                try:
                    nested_response = await safe_fetch(client, expand_url)
                except Exception as exc:
                    logger.debug("tbank.expand_url_fetch_failed", url=expand_url, error=str(exc))
                    continue
                nested_urls = _extract_detail_urls(
                    str(nested_response.text),
                    str(getattr(nested_response, "url", expand_url) or expand_url),
                    limit=limit,
                    detail_re=detail_re,
                    listing_re=listing_re,
                )
                _merge(nested_urls)
                if len(collected) >= limit:
                    return collected[:limit]
        except Exception as exc:
            logger.debug(
                "tbank.http_discover_failed_escalating_to_browser", url=spec.url, error=str(exc)
            )

        bypass_strategy = spec.monitor_config.get("_bypass_strategy")
        browser_config = resolve_browser_config(spec, bypass_strategy)
        async with open_page(browser_config, bypass_strategy=bypass_strategy) as page:
            await navigate(page, spec.url, browser_config)
            current_url = getattr(page, "url", spec.url) or spec.url
            current_url_candidates = _extract_detail_urls_from_hrefs(
                [current_url],
                current_url,
                limit=1,
                detail_re=detail_re,
                listing_re=listing_re,
            )
            if current_url_candidates:
                return current_url_candidates
            browser = getattr(getattr(self, "_manifest_entry", None), "browser", None)
            try:
                browser_urls = await browser_scroll_collect_urls(
                    page,
                    current_url,
                    detail_re,
                    limit=limit,
                    scroll_loops=getattr(browser, "scroll_loops", None) or 8,
                    pause_sec=(getattr(browser, "scroll_pause_ms", None) or 500) / 1000.0,
                )
            except Exception as exc:
                if collected:
                    logger.debug(
                        "tbank.browser_scroll_failed_returning_partial",
                        url=spec.url,
                        collected=len(collected),
                        error=str(exc),
                    )
                    return collected[:limit]
                raise
            _merge(
                _extract_detail_urls_from_hrefs(
                    browser_urls,
                    current_url,
                    limit=limit,
                    detail_re=detail_re,
                    listing_re=listing_re,
                )
            )
            return collected[:limit]

    @property
    def __name__(self) -> str:
        return "TbankCareerParser"


register_site_parser(
    "tbank_career",
    domain_pattern=TbankCareerParser.domain_pattern,
    assessment_hint=known_board_assessment_hint("known_site", "site_parser:tbank.ru"),
)(TbankCareerParser)
