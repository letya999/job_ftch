"""Discover-only parser for hirify.me."""

from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qsl, urljoin, urlparse

import httpx
import structlog

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.infrastructure.sources.browser_utils import navigate, open_page
from job_ftch.infrastructure.sources.http_retry import fetch_with_retry
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    browser_scroll_collect_urls,
    extract_urls_with_limit,
    normalize_search_keywords,
    resolve_browser_config,
    safe_fetch,
    with_query_params,
)

if TYPE_CHECKING:
    from job_ftch.domain.source_spec import CareerSiteSpec

logger = structlog.get_logger(__name__)

_DETAIL_URL_RE = re.compile(r"/jobs/[a-z0-9-]*\d[a-z0-9-]*", re.IGNORECASE)
_NUXT_CONFIG_RE = re.compile(
    r"window\.__NUXT__\.config=\{public:\{apiBase:\"([^\"]+)\"",
    re.IGNORECASE,
)
_QUERY_BY_PATH: dict[str, dict[str, str]] = {
    "/jobs-in-russia": {"countries": "russia", "regions": "russia"},
    "/jobs-in-europe": {"regions": "europe", "excluded_countries": "russia,belarus"},
}


class HirifyRateLimitedError(RuntimeError):
    """Raised when Hirify returns its application-level rate-limit response."""


def _extract_detail_urls(html: str, base_url: str, *, limit: int) -> list[str]:
    return extract_urls_with_limit(html, _DETAIL_URL_RE, base_url, limit)


class HirifyParser:
    domain_pattern = r"^https?://(?:www\.)?hirify\.me(?:/|$)"
    has_custom_parse = True
    supports_discover = True
    supports_search = True
    search_mode = "combined"

    def build_search_urls(
        self,
        base_url: str,
        keywords: Any,
        *,
        limit: int | None = None,
    ) -> list[str]:
        del limit
        terms = normalize_search_keywords(keywords)
        if not terms:
            return []
        # Hirify searches via its API; the page URL carries `search`/`params`
        # which `_query_for_spec` forwards to /api/vacancies. Its search box
        # treats a bare space-joined string as all-terms (matches nothing) but
        # honours a lowercase " or " operator between roles (verified live).
        return [
            with_query_params(
                base_url,
                {"search": " or ".join(terms), "params": "title,company"},
            )
        ]

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(include_if_detail_page=True, render=True, wait="networkidle")

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    def _api_url(self, html: str) -> str:
        match = _NUXT_CONFIG_RE.search(html)
        api_base = match.group(1).rstrip("/") if match else "https://api.hirify.me"
        return f"{api_base}/api/vacancies"

    def _query_for_spec(self, spec: CareerSiteSpec) -> dict[str, str]:
        parsed = urlparse(spec.url)
        query = dict(_QUERY_BY_PATH.get(parsed.path.rstrip("/") or "/", {}))
        if "countries" not in query and "russia" in parsed.path.casefold():
            query["countries"] = "russia"
        if "regions" not in query and "russia" in parsed.path.casefold():
            query["regions"] = "russia"
        # Forward an explicit keyword search (set by build_search_urls or the
        # operator) onto the vacancies API so hirify filters by title/company.
        url_params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        for key in ("search", "params", "countries", "regions", "excluded_countries"):
            value = url_params.get(key)
            if value and value.strip():
                query[key] = value
        return query

    async def _discover_via_api(
        self,
        spec: CareerSiteSpec,
        client: Any,
        html: str,
    ) -> list[str]:
        query = self._query_for_spec(spec)
        if not query:
            return []
        response = await fetch_with_retry(
            client,
            self._api_url(html),
            params=query,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Referer": spec.url,
                "Origin": "https://hirify.me",
            },
            follow_redirects=True,
        )
        if response.status_code == 429:
            raise HirifyRateLimitedError("429 Too many requests from hirify api")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        if not isinstance(data, list):
            return []
        urls: list[str] = []
        seen: set[str] = set()
        for row in data:
            if not isinstance(row, dict):
                continue
            raw_url = row.get("url") or row.get("job_url") or row.get("vacancy_url")
            slug = row.get("slug")
            vacancy_id = row.get("id") or row.get("vacancy_id")
            if not raw_url and slug:
                raw_url = f"/jobs/{slug}"
            elif not raw_url and vacancy_id and slug:
                raw_url = f"/jobs/{vacancy_id}-{slug}"
            elif not raw_url and vacancy_id:
                raw_url = f"/jobs/{vacancy_id}"
            if not isinstance(raw_url, str) or not raw_url:
                continue
            absolute = urljoin(spec.url, raw_url.split("?", 1)[0])
            if absolute in seen or not _DETAIL_URL_RE.search(urlparse(absolute).path):
                continue
            seen.add(absolute)
            urls.append(absolute)
            if len(urls) >= (spec.limit or 50):
                break
        return urls

    async def _discover_via_browser_api(
        self,
        page: Any,
        spec: CareerSiteSpec,
        html: str,
    ) -> list[str]:
        query = self._query_for_spec(spec)
        if not query:
            return []
        api_url = self._api_url(html)
        param_string = "&".join(f"{key}={value}" for key, value in query.items())
        target_url = f"{api_url}?{param_string}"

        async def _fetch_once() -> dict[str, Any]:
            script = """
                async (targetUrl) => {
                    const response = await fetch(targetUrl, {
                        credentials: "include",
                        headers: { accept: "application/json, text/plain, */*" },
                    });
                    return {
                        status: response.status,
                        text: await response.text(),
                    };
                }
            """
            return cast("dict[str, Any]", await page.evaluate(script, target_url))

        result = await _fetch_once()
        if int(result.get("status") or 0) == 429:
            countdown = await page.evaluate(
                """
                () => {
                    const bodyText = document.body?.innerText || "";
                    const match = bodyText.match(/\\b(\\d{1,3})\\s+seconds?\\b/i);
                    return match ? Number(match[1]) : null;
                }
                """
            )
            if isinstance(countdown, (int, float)) and countdown > 0:
                wait_seconds = min(int(countdown) + 2, 70)
                logger.info("hirify_browser_waiting_for_rate_limit", seconds=wait_seconds)
                await asyncio.sleep(wait_seconds)
                result = await _fetch_once()
        if int(result.get("status") or 0) == 429:
            raise HirifyRateLimitedError("429 Too many requests from hirify browser api")
        if int(result.get("status") or 0) >= 400:
            raise RuntimeError(
                f"browser api fetch failed with status {result.get('status')}: {result.get('text', '')[:200]}"
            )
        payload = json.loads(str(result.get("text") or "{}"))
        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        if not isinstance(data, list):
            return []
        urls: list[str] = []
        seen: set[str] = set()
        for row in data:
            if not isinstance(row, dict):
                continue
            raw_url = row.get("url") or row.get("job_url") or row.get("vacancy_url")
            slug = row.get("slug")
            vacancy_id = row.get("id") or row.get("vacancy_id")
            if not raw_url and slug:
                raw_url = f"/jobs/{slug}"
            elif not raw_url and vacancy_id and slug:
                raw_url = f"/jobs/{vacancy_id}-{slug}"
            elif not raw_url and vacancy_id:
                raw_url = f"/jobs/{vacancy_id}"
            if not isinstance(raw_url, str) or not raw_url:
                continue
            absolute = urljoin(spec.url, raw_url.split("?", 1)[0])
            if absolute in seen or not _DETAIL_URL_RE.search(urlparse(absolute).path):
                continue
            seen.add(absolute)
            urls.append(absolute)
            if len(urls) >= (spec.limit or 50):
                break
        return urls

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        response = await safe_fetch(client, spec.url)
        current_url = str(response.url)
        if _DETAIL_URL_RE.search(current_url):
            return [current_url.split("?", 1)[0]]
        html = str(response.text)
        # When a keyword search is requested, the links baked into the page HTML
        # are the default unfiltered feed - going straight to the API is the only
        # way the `search` filter is actually applied. Only take the HTML links
        # directly when there is no search query.
        has_search = bool(self._query_for_spec(spec).get("search"))
        if not has_search:
            direct_urls = _extract_detail_urls(html, current_url, limit=spec.limit or 50)
            if direct_urls:
                return direct_urls

        rate_limited = False
        try:
            api_urls = await self._discover_via_api(spec, client, html)
            if api_urls:
                return api_urls
        except HirifyRateLimitedError:
            rate_limited = True
            logger.warning("hirify_api_rate_limited", url=spec.url)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            logger.debug("hirify_api_invalid_payload", url=spec.url, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.debug("hirify_api_discover_failed", url=spec.url, error=str(exc))

        bypass_strategy = spec.monitor_config.get("_bypass_strategy")
        browser_config = resolve_browser_config(spec, bypass_strategy)
        async with open_page(browser_config, bypass_strategy=bypass_strategy) as page:
            await navigate(page, spec.url, browser_config)
            page_url = getattr(page, "url", spec.url) or spec.url
            if _DETAIL_URL_RE.search(urlparse(page_url).path):
                return [page_url.split("?", 1)[0]]
            try:
                browser_api_urls = await self._discover_via_browser_api(page, spec, html)
                if browser_api_urls:
                    return browser_api_urls
            except HirifyRateLimitedError:
                rate_limited = True
                logger.warning("hirify_browser_api_rate_limited", url=spec.url)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                logger.debug("hirify_browser_api_invalid_payload", url=spec.url, error=str(exc))
            except Exception as exc:  # noqa: BLE001
                logger.debug("hirify_browser_api_failed", url=spec.url, error=str(exc))
            if rate_limited:
                request = httpx.Request("GET", self._api_url(html))
                response_429 = httpx.Response(
                    429,
                    request=request,
                    text="Too many requests. Please try again later.",
                )
                raise httpx.HTTPStatusError(
                    "hirify api rate limited after browser fallback",
                    request=request,
                    response=response_429,
                )
            return await browser_scroll_collect_urls(
                page,
                page_url,
                _DETAIL_URL_RE,
                limit=spec.limit or 50,
                scroll_loops=4,
                pause_sec=0.75,
            )

    @property
    def __name__(self) -> str:
        return "HirifyParser"


register_site_parser(
    "hirify",
    domain_pattern=HirifyParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:hirify.me",
        has_stable_url=True,
        supports_ordered_head=True,
        can_detect_freshness_without_snapshot=False,
        ordered_by_newest=False,
        requires_full_snapshot=False,
        rationale="hirify.me exposes a first-party vacancies API behind the feed UI and keeps canonical /jobs detail URLs for individual vacancies.",
    ),
)(HirifyParser)
