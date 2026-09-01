"""Rich API parser for hirify.me, with the generic crawl as fallback.

hirify.me is a Nuxt SPA. Its listing endpoint carries structured fields but no
prose, and the rendered detail page exposes only the card chrome and tag chips -
scraping it yielded a 254-character "description" made of "Show contacts",
"Report" and a keyword list, while the real posting runs to a couple of thousand
characters. The body lives behind a second call, ``/api/vacancies/{id}``, whose
``text`` field holds the full HTML.

So this parser fetches the listing API, then the per-vacancy detail, and emits
complete items. The page is only needed to discover a custom API base; when it
is unavailable the documented default API host is still tried. When the API is
unavailable it yields nothing, which lets
``CareerSiteSource`` fall through to the generic crawl rather than reimplement
bypass and browser escalation here. That fallback is why neither
``confirmed_empty_on_empty`` nor ``terminal_on_empty`` is set.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qsl, urljoin, urlparse

import httpx
import structlog
from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.domain import (
    AcquisitionTransport,
    ObservationKind,
    SourceFamily,
    SourceIdentity,
    SourceKind,
    source_spec_name,
)
from job_ftch.infrastructure.sources.browser_utils import navigate, open_page
from job_ftch.infrastructure.sources.http_retry import fetch_with_retry
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
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

# Detail bodies are fetched one request per vacancy; hirify rate-limits at the
# application level (see HirifyRateLimitedError), so keep the fan-out modest.
_DETAIL_CONCURRENCY = 4

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


def _html_to_text(value: str) -> str:
    """Flatten the ``text`` field, which hirify stores as HTML fragments."""
    if not value.strip():
        return ""
    parsed = LexborHTMLParser(value)
    lines = [
        line.strip()
        for line in parsed.text(separator="\n", strip=True).splitlines()
        if line.strip()
    ]
    return "\n".join(lines)


_NAME_KEYS = ("name", "name_en", "title", "value")


def _names(rows: Any, *keys: str) -> list[str]:
    """Pull display names out of hirify's `[{id, name, ...}]` lookup lists.

    Entries are either plain strings (``work_format``) or lookup dicts
    (``grades``, ``tags``, ``specializations``), so both shapes are handled.
    """
    out: list[str] = []
    if not isinstance(rows, list):
        return out
    lookup = keys or _NAME_KEYS
    for row in rows:
        if isinstance(row, str) and row.strip():
            out.append(row.strip())
        elif isinstance(row, dict):
            for key in lookup:
                value = row.get(key)
                if isinstance(value, str) and value.strip():
                    out.append(value.strip())
                    break
    return out


def _id_from_detail_url(url: str) -> str | None:
    """`/jobs/668118-product-owner-ai-platform` -> `668118`."""
    match = re.search(r"/jobs/(\d+)", urlparse(url).path)
    return match.group(1) if match else None


def _detail_url(row: dict[str, Any]) -> str | None:
    slug = row.get("slug")
    vacancy_id = row.get("id")
    if isinstance(slug, str) and slug.strip():
        return f"https://hirify.me/jobs/{slug.strip()}"
    if vacancy_id is not None:
        return f"https://hirify.me/jobs/{vacancy_id}"
    return None


def _posted_at(row: dict[str, Any]) -> datetime | None:
    raw = row.get("created_at")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _salary_metadata(row: dict[str, Any]) -> dict[str, Any] | None:
    salary = row.get("salary")
    if not isinstance(salary, dict):
        return None
    minimum, maximum = salary.get("min"), salary.get("max")
    if minimum is None and maximum is None:
        return None
    return {
        "min": minimum,
        "max": maximum,
        "currency": salary.get("currency"),
        "unit": "month",
    }


class HirifyParser:
    domain_pattern = r"^https?://(?:www\.)?hirify\.me(?:/|$)"
    has_custom_parse = True
    # `parse` owns the flow so the body can come from the per-vacancy endpoint;
    # `discover` remains its URL-recovery path when the listing API is down.
    supports_discover = False
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
        # Hirify has no `/vacancies` page; keep real category routes, but make
        # generic DB entries using that common path land on the working home.
        if parsed.path.rstrip("/") == "/vacancies":
            base_url = parsed._replace(path="/").geturl()
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

    async def _fetch_listing_rows(
        self,
        spec: CareerSiteSpec,
        client: Any,
        html: str,
    ) -> list[dict[str, Any]]:
        response = await fetch_with_retry(
            client,
            self._api_url(html),
            params=self._query_for_spec(spec),
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
        data = payload.get("data") if isinstance(payload, dict) else None
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []

    async def _fetch_detail_body(
        self,
        client: Any,
        html: str,
        vacancy_id: Any,
        referer: str,
    ) -> dict[str, Any] | None:
        """Fetch one vacancy's body. Returns None so a single failure is skipped."""
        try:
            response = await fetch_with_retry(
                client,
                f"{self._api_url(html)}/{vacancy_id}",
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Referer": referer,
                    "Origin": "https://hirify.me",
                },
                follow_redirects=True,
            )
            if response.status_code == 429:
                raise HirifyRateLimitedError("429 Too many requests from hirify detail api")
            response.raise_for_status()
            payload = response.json()
        except HirifyRateLimitedError:
            raise
        except Exception as exc:  # noqa: BLE001 - one bad vacancy must not sink the run
            logger.debug("hirify_detail_fetch_failed", vacancy_id=vacancy_id, error=str(exc))
            return None
        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            payload = payload["data"]
        return payload if isinstance(payload, dict) else None

    def _to_raw_item(
        self,
        row: dict[str, Any],
        detail: dict[str, Any] | None,
        source_name: str,
    ) -> Any:
        # The detail response repeats most listing fields but leaves some of the
        # lookup lists empty, so a plain overlay would drop grades and tags that
        # the listing did carry. Only let detail win where it has content.
        merged = dict(row)
        for key, value in (detail or {}).items():
            if value not in (None, "", [], {}):
                merged[key] = value
        title = str(merged.get("title") or merged.get("original_title") or "").strip()
        url = _detail_url(merged)
        if not title or not url:
            return None

        # `text` is the posting itself. `tldr` is hirify's own summary and is the
        # only body available when the detail call failed - thin, but honest.
        body = _html_to_text(str(merged.get("text") or ""))
        if not body:
            body = str(merged.get("clear_text") or "").strip() or str(merged.get("tldr") or "")
        if not body:
            return None

        company = str(merged.get("company_title") or "").strip()
        sections = [title, company, body]

        work_format = _names(merged.get("work_format"))
        regions = _names(merged.get("regions"), "name", "name_en")
        metadata: dict[str, Any] = {
            "source_family": SourceFamily.ATS_API.value,
            "observation_kind": ObservationKind.VACANCY_DETAIL.value,
            "transport": AcquisitionTransport.HTTP.value,
            "adapter": "hirify-api",
            "parser": "hirify-api",
            "parser_version": "hirify-api-v1",
            "detail_vacancy_confirmed": bool(merged.get("text")),
            "company": company or None,
            "locations": regions or None,
            "work_modes": work_format or None,
            "employment_type": str(merged.get("work_type") or "") or None,
            "seniority_hints": _names(merged.get("grades")) or None,
            "specializations": _names(merged.get("specializations"), "name", "name_en") or None,
            "skills": _names(merged.get("tags")) or None,
            "base_salary": _salary_metadata(merged),
            "date_posted": merged.get("created_at"),
            "detected_language": str(merged.get("vacancy_language") or "") or None,
            "is_scam": merged.get("is_scam"),
        }

        return build_raw_item(
            source_kind=SourceKind.CAREER_SITE,
            source_name=source_name,
            external_id=str(merged.get("id") or ""),
            url=url,
            text="\n\n".join(section for section in sections if section),
            created_at=_posted_at(merged),
            metadata=metadata,
            source_identity=SourceIdentity(
                family=SourceFamily.ATS_API,
                observation_kind=ObservationKind.VACANCY_DETAIL,
                transport=AcquisitionTransport.HTTP,
                adapter="hirify-api",
                parser_version="hirify-api-v1",
                legacy_kind=SourceKind.CAREER_SITE.value,
            ),
        )

    async def parse(self, spec: CareerSiteSpec, client: Any) -> Any:
        """Yield complete vacancies from the API.

        Yielding nothing is deliberate on failure: CareerSiteSource then falls
        through to the generic crawl, which owns bypass and browser escalation.
        """
        try:
            response = await safe_fetch(client, spec.url)
            html = str(response.text)
        except Exception as exc:  # noqa: BLE001 - API has a documented default host
            logger.info("hirify_page_fetch_failed", url=spec.url, error=str(exc))
            html = ""

        try:
            rows = await self._fetch_listing_rows(spec, client, html)
        except HirifyRateLimitedError:
            logger.warning("hirify_api_rate_limited", url=spec.url)
            return
        except Exception as exc:  # noqa: BLE001
            logger.info("hirify_api_listing_failed", url=spec.url, error=str(exc))
            rows = []

        if not rows:
            # Listing API unavailable. Discovery still has HTML-link and browser
            # paths, and the per-vacancy endpoint is separate, so an id is often
            # enough to recover the body without the listing.
            try:
                urls = await self.discover(spec, client)
            except Exception as exc:  # noqa: BLE001
                logger.info("hirify_discover_fallback_failed", url=spec.url, error=str(exc))
                return
            rows = [
                {"id": vacancy_id, "slug": urlparse(url).path.rsplit("/", 1)[-1]}
                for url in urls
                if (vacancy_id := _id_from_detail_url(url))
            ]
        if not rows:
            return

        rows = rows[: spec.limit or 50]
        semaphore = asyncio.Semaphore(_DETAIL_CONCURRENCY)

        async def _detail(row: dict[str, Any]) -> dict[str, Any] | None:
            vacancy_id = row.get("id")
            if vacancy_id is None:
                return None
            async with semaphore:
                return await self._fetch_detail_body(
                    client, html, vacancy_id, _detail_url(row) or spec.url
                )

        try:
            details = await asyncio.gather(*(_detail(row) for row in rows))
        except HirifyRateLimitedError:
            logger.warning("hirify_detail_api_rate_limited", url=spec.url)
            return

        source_name = spec.source_name or source_spec_name(spec)
        emitted = 0
        for row, detail in zip(rows, details, strict=True):
            item = self._to_raw_item(row, detail, source_name)
            if item is not None:
                emitted += 1
                yield item
        logger.info("hirify_api_parsed", url=spec.url, rows=len(rows), emitted=emitted)

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
