"""Site-specific parser for yandex.ru/jobs.

Yandex Jobs exposes a public REST API at ``/jobs/api/publications``. The
``text=`` query actually narrows results (``q=`` / ``search=`` are ignored).
HTTP API is the primary path; SSR listing and browser intercept remain
fallbacks when the API is empty or unreachable.
"""

from __future__ import annotations

import asyncio
import html
import inspect
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urljoin, urlparse, urlunparse

import structlog
from selectolax.parser import HTMLParser

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    keywords_from_spec,
    normalize_search_keywords,
    text_matches_keywords,
    with_query_params,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

logger = structlog.get_logger(__name__)

_STATUS_RE = re.compile(r"\b(?:status|error)\s+(\d{3})\b", re.IGNORECASE)
_VACANCY_LINK_RE = re.compile(
    r"/jobs/vacancies/(?!city_|team_|office_|service_|profession_|tag_)([a-z0-9-]+-\d+)",
    re.IGNORECASE,
)
_NON_VACANCY_TITLE_RE = re.compile(
    r"\b(?:карьерн\w*\s+консультац\w*|стажировк\w*|мероприят\w*)\b", re.IGNORECASE
)


def _clean_text(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(value.split())


def _item_from_api(payload: dict[str, Any], base_url: str, source_name: str) -> RawItem | None:
    """Convert a single /api/publications result dict into a RawItem."""
    title = _clean_text(payload.get("title"))
    if not title or _NON_VACANCY_TITLE_RE.search(title):
        return None

    slug = payload.get("publication_slug_url") or ""
    vacancy_id = payload.get("id")
    job_url = urljoin(base_url, slug) if slug else base_url

    # Extract structured metadata from nested API objects
    vacancy = payload.get("vacancy") or {}
    cities = [c.get("name", "") for c in vacancy.get("cities", []) if c.get("name")]
    skills = [s.get("name", "") for s in vacancy.get("skills", []) if s.get("name")]
    work_modes = [w.get("name", "") for w in vacancy.get("work_modes", []) if w.get("name")]

    public_service = payload.get("public_service") or {}
    service_name = public_service.get("name", "")
    service_group = (public_service.get("group") or {}).get("name", "")

    short_summary = _clean_text(payload.get("short_summary"))

    text_parts = [title, short_summary, service_name, service_group, *cities, *skills]
    text = "\n".join(p for p in text_parts if p)

    return build_raw_item(
        source_kind=SourceKind.CAREER_SITE,
        source_name=source_name,
        external_id=str(vacancy_id or slug or job_url),
        url=job_url,
        text=text,
        metadata={
            "board_url": base_url,
            "job_url": job_url,
            "service": service_name,
            "service_group": service_group,
            "cities": cities,
            "skills": skills,
            "work_modes": work_modes,
            "parser": "site_yandex_jobs",
            "detail_vacancy_confirmed": True,
        },
    )


def _extract_ssr_vacancy_urls(html_text: str, base_url: str, *, limit: int) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for slug in _VACANCY_LINK_RE.findall(html.unescape(html_text)):
        url = urljoin(base_url, f"/jobs/vacancies/{slug}")
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= limit:
            break
    return urls


def _item_from_detail_html(detail_url: str, html_text: str, source_name: str) -> RawItem | None:
    tree = HTMLParser(html_text)
    title_node = tree.css_first("h1")
    main_node = tree.css_first("main")
    title = _clean_text(title_node.text(strip=True) if title_node else None)
    main_text = _clean_text(main_node.text(separator=" ", strip=True) if main_node else None)
    if not title or _NON_VACANCY_TITLE_RE.search(title):
        return None

    text = "\n".join(part for part in (title, main_text) if part)
    external_id = detail_url.rstrip("/").rsplit("-", 1)[-1]
    return build_raw_item(
        source_kind=SourceKind.CAREER_SITE,
        source_name=source_name,
        external_id=external_id,
        url=detail_url,
        text=text,
        metadata={
            "job_url": detail_url,
            "parser": "site_yandex_jobs",
            "detail_vacancy_confirmed": True,
        },
    )


@register_site_parser(
    "yandex_jobs",
    domain_pattern=r"yandex\.ru/jobs",
    assessment_hint=known_board_assessment_hint("known_site", "site_parser:yandex.ru"),
)
class YandexJobsParser:
    domain_pattern = r"yandex\.ru/jobs"
    has_custom_parse = True
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
        parsed = urlparse(base_url)
        path = (parsed.path or "").rstrip("/")
        if path in {"", "/jobs"} or not path.endswith("/vacancies"):
            parsed = parsed._replace(path="/jobs/vacancies")
        listing = urlunparse(parsed._replace(query="", fragment=""))
        return [with_query_params(listing, {"text": " OR ".join(terms)})]

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(render=True, wait="domcontentloaded")

    def parser_kind(self, url: str) -> str | None:
        del url
        return "yandex_jobs"

    def _api_path(self) -> str:
        manifest_entry = getattr(self, "_manifest_entry", None)
        return getattr(manifest_entry, "api_path", None) or "/jobs/api/publications"

    def _limit(self, spec_limit: int | None, settings_limit: int) -> int:
        manifest_entry = getattr(self, "_manifest_entry", None)
        manifest_limit = getattr(manifest_entry, "limit", None)
        return spec_limit or manifest_limit or settings_limit

    def _browser_scroll_config(self) -> tuple[int, float, int, int]:
        manifest_entry = getattr(self, "_manifest_entry", None)
        browser = getattr(manifest_entry, "browser", None)
        loops = getattr(browser, "scroll_loops", None) or 8
        pause_ms = getattr(browser, "scroll_pause_ms", None) or 1200
        scroll_px = getattr(browser, "scroll_px", None) or 3000
        stale_rounds = getattr(browser, "stale_rounds", None) or 4
        return loops, pause_ms / 1000.0, scroll_px, stale_rounds

    def _detail_timeout_ms(self, default_timeout_ms: int) -> int:
        manifest_entry = getattr(self, "_manifest_entry", None)
        return (
            getattr(manifest_entry, "detail_timeout_ms", None)
            or getattr(manifest_entry, "timeout_ms", None)
            or default_timeout_ms
        )

    @staticmethod
    def _search_text(url: str) -> str:
        query = dict(parse_qsl(urlparse(url).query, keep_blank_values=True))
        return str(query.get("text") or "").strip()

    async def _publications_from_api(
        self,
        client: Any,
        listing_url: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        origin = urlparse(listing_url)
        api_url = urlunparse(origin._replace(path=self._api_path(), query="", fragment=""))
        page_size = min(max(limit, 1), 50)
        text = self._search_text(listing_url)
        collected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for page in range(1, 6):
            params = {"page_size": str(page_size)}
            if page > 1:
                params["page"] = str(page)
            if text:
                params["text"] = text
            try:
                response = await client.get(
                    with_query_params(api_url, params),
                    follow_redirects=True,
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:  # noqa: BLE001 - SSR/browser remain fallbacks
                logger.debug("yandex.api_listing_failed", url=api_url, error=str(exc))
                break
            if not isinstance(payload, dict):
                break
            rows = (
                payload.get("results") or payload.get("items") or payload.get("publications") or []
            )
            if not isinstance(rows, list) or not rows:
                break
            new_on_page = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                row_id = str(row.get("id") or row.get("publication_id") or row.get("url") or "")
                if row_id and row_id in seen:
                    continue
                if row_id:
                    seen.add(row_id)
                collected.append(row)
                new_on_page += 1
                if len(collected) >= limit:
                    return collected[:limit]
            if len(rows) < page_size or new_on_page == 0:
                break
        return collected[:limit]

    async def parse(
        self,
        spec: CareerSiteSpec,
        client: Any,
    ) -> AsyncIterator[RawItem]:
        from job_ftch.config import get_settings
        from job_ftch.infrastructure.sources.browser_utils import (
            BROWSER_KEYS,
            navigate,
            open_page,
            safe_content,
        )

        settings = get_settings()
        limit = self._limit(spec.limit, settings.career_site_default_limit)
        source_name = spec.source_name or "yandex_jobs"
        api_path = self._api_path()
        scroll_loops, scroll_pause_sec, scroll_px, stale_round_limit = self._browser_scroll_config()
        detail_timeout_ms = self._detail_timeout_ms(
            int(settings.career_site_timeout_seconds * 1_000)
        )

        listing_url = spec.url
        # The locale-neutral root returns a marketing shell with HTTP 200, so
        # the new fallback never ran and the parser entered browser discovery
        # with no vacancy links.  Normalise it before the initial SSR request.
        if listing_url.rstrip("/") in {"https://yandex.ru/jobs", "https://www.yandex.ru/jobs"}:
            listing_url = "https://yandex.ru/jobs/vacancies"

        emitted = 0
        emitted_urls: set[str] = set()
        search_text = self._search_text(listing_url)
        # ``text=`` already narrowed the API/listing. Re-filtering titles
        # against that query drops valid cards (``Data Analyst`` vs ``ai``).
        keywords = [] if search_text else keywords_from_spec(spec)
        for payload in await self._publications_from_api(client, listing_url, limit):
            api_item = _item_from_api(payload, listing_url, source_name)
            if api_item is None or str(api_item.url) in emitted_urls:
                continue
            if keywords and not text_matches_keywords(api_item.text, keywords):
                continue
            yield api_item
            emitted += 1
            emitted_urls.add(str(api_item.url))
            if emitted >= limit:
                break
        if emitted:
            logger.info("yandex_parser_api_emitted", url=listing_url, emitted=emitted)
            return

        try:
            listing_response = await client.get(listing_url, follow_redirects=True)
            listing_response.raise_for_status()
            ssr_urls = _extract_ssr_vacancy_urls(
                str(listing_response.text), str(listing_response.url), limit=limit
            )
        except Exception as exc:
            logger.debug(
                "yandex.ssr_listing_failed_trying_fallback", url=listing_url, error=str(exc)
            )
            if "/jobs/vacancies/" in listing_url:
                listing_url = "https://yandex.ru/jobs/"
            try:
                listing_response = await client.get(listing_url, follow_redirects=True)
                listing_response.raise_for_status()
                ssr_urls = _extract_ssr_vacancy_urls(
                    str(listing_response.text), str(listing_response.url), limit=limit
                )
            except Exception as exc2:
                logger.warning(
                    "yandex.ssr_listing_fallback_also_failed", url=listing_url, error=str(exc2)
                )
                ssr_urls = []

        for detail_url in ssr_urls:
            try:
                detail_response = await client.get(detail_url, follow_redirects=True)
                detail_response.raise_for_status()
            except Exception as exc:
                logger.debug("yandex.detail_fetch_failed", url=detail_url, error=str(exc))
                continue
            item = _item_from_detail_html(
                str(detail_response.url), str(detail_response.text), source_name
            )
            if item is None:
                continue
            if keywords and not text_matches_keywords(item.text, keywords):
                continue
            yield item
            emitted += 1
            emitted_urls.add(str(item.url))
            if emitted >= limit:
                break
        if emitted:
            logger.info("yandex_parser_ssr_emitted", url=listing_url, emitted=emitted)
            return
        if search_text:
            logger.info("yandex_parser_empty_search", url=listing_url, emitted=emitted)
            return

        browser_config = {k: v for k, v in spec.monitor_config.items() if k in BROWSER_KEYS}
        browser_config.setdefault("headless", True)
        browser_config.setdefault("stealth", True)
        browser_config.setdefault("wait", "domcontentloaded")
        bypass_strategy = spec.monitor_config.get("_bypass_strategy")

        collected: list[dict[str, Any]] = []
        browser_ssr_urls: list[str] = []
        seen_ids: set[int] = set()
        attempts = max(1, len(getattr(bypass_strategy, "available_tiers", ()) or ()))

        for _ in range(attempts):
            try:
                async with open_page(
                    browser_config,
                    bypass_strategy=bypass_strategy,
                ) as page:

                    async def _on_response(response: Any) -> None:
                        if api_path not in response.url:
                            return
                        try:
                            body = await response.json()
                        except Exception:
                            return
                        results = (
                            body.get("results")
                            or body.get("items")
                            or body.get("publications")
                            or []
                        )
                        for item in results:
                            if len(collected) + emitted >= limit:
                                break
                            vid = item.get("id")
                            if vid and vid not in seen_ids:
                                seen_ids.add(vid)
                                collected.append(item)

                    page.on("response", _on_response)

                    await navigate(page, listing_url, browser_config)
                    last_height = 0
                    stale_rounds = 0
                    for _ in range(scroll_loops):
                        if len(collected) + emitted >= limit:
                            break
                        current_height = await page.evaluate("() => document.body.scrollHeight")
                        if current_height == last_height:
                            stale_rounds += 1
                            if stale_rounds >= stale_round_limit:
                                break
                        else:
                            stale_rounds = 0
                        last_height = current_height
                        await page.evaluate(f"() => window.scrollBy(0, {scroll_px})")
                        await asyncio.sleep(scroll_pause_sec)
                    if not collected:
                        browser_ssr_urls = _extract_ssr_vacancy_urls(
                            await safe_content(page),
                            listing_url,
                            limit=limit,
                        )
                    break
            except Exception as exc:
                if bypass_strategy is None or not hasattr(bypass_strategy, "handle_failure"):
                    logger.info("yandex_parser_navigation_failed", url=spec.url, error=str(exc))
                    break
                previous_tier = getattr(bypass_strategy, "current_name", None)
                status_code = _status_from_error(exc)
                failure_result = bypass_strategy.handle_failure(
                    listing_url,
                    status_code=status_code,
                    body=None,
                    error=exc,
                )
                failure_kind = (
                    await failure_result if inspect.isawaitable(failure_result) else failure_result
                )
                current_tier = getattr(bypass_strategy, "current_name", None)
                logger.info(
                    "yandex_parser_navigation_failed",
                    url=spec.url,
                    error=str(exc),
                    failure_kind=failure_kind,
                    previous_tier=previous_tier,
                    current_tier=current_tier,
                )
                if current_tier == previous_tier:
                    break

        logger.info(
            "yandex_parser_api_collected",
            url=spec.url,
            api_items=len(collected),
            limit=limit,
        )

        for payload in collected:
            if emitted >= limit:
                break
            api_item = _item_from_api(payload, spec.url, source_name)
            if api_item is None or str(api_item.url) in emitted_urls:
                continue
            item = api_item
            try:
                detail_response = await client.get(str(api_item.url), follow_redirects=True)
                detail_response.raise_for_status()
                item = (
                    _item_from_detail_html(
                        str(detail_response.url), str(detail_response.text), source_name
                    )
                    or api_item
                )
            except Exception as exc:
                logger.debug(
                    "yandex.detail_enrich_failed_using_api_record",
                    url=str(api_item.url),
                    error=str(exc),
                )
            if str(item.url) in emitted_urls:
                continue
            yield item
            emitted += 1
            emitted_urls.add(str(item.url))
        if emitted < limit and not collected and browser_ssr_urls:
            logger.info(
                "yandex_parser_browser_ssr_fallback",
                url=spec.url,
                ssr_urls=len(browser_ssr_urls),
            )
            try:
                async with open_page(
                    browser_config,
                    bypass_strategy=bypass_strategy,
                ) as page:
                    for detail_url in browser_ssr_urls:
                        response = await page.goto(
                            detail_url,
                            wait_until="domcontentloaded",
                            timeout=detail_timeout_ms,
                        )
                        status = int(response.status) if response is not None else 0
                        if status >= 400:
                            continue
                        item = _item_from_detail_html(
                            detail_url, await safe_content(page), source_name
                        )
                        if item is None:
                            continue
                        if str(item.url) in emitted_urls:
                            continue
                        yield item
                        emitted += 1
                        emitted_urls.add(str(item.url))
                        if emitted >= limit:
                            break
            except Exception as exc:
                logger.info(
                    "yandex_parser_browser_ssr_failed",
                    url=spec.url,
                    error=str(exc),
                )
        logger.info("yandex_parser_emitted", url=spec.url, emitted=emitted)


def _status_from_error(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    if response is not None:
        return getattr(response, "status_code", None)
    match = _STATUS_RE.search(str(exc))
    if match:
        return int(match.group(1))
    return None
