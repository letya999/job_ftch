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
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

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
from job_ftch.infrastructure.sources.http_retry import fetch_with_retry, parse_retry_after
from job_ftch.infrastructure.sources.monitors.shared import (
    BrowserChallengeError,
    raise_if_browser_challenge,
)
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    browser_scroll_collect_urls,
    extract_urls_with_limit,
    is_challenge_response,
    keywords_from_spec,
    listing_matches_keywords,
    normalize_search_keywords,
    resolve_browser_config,
    safe_fetch,
    with_query_params,
)
from job_ftch.infrastructure.sources.source_deadline import (
    remaining_source_seconds,
    sleep_with_source_deadline,
)

if TYPE_CHECKING:
    from job_ftch.domain.source_spec import CareerSiteSpec

logger = structlog.get_logger(__name__)

# Detail bodies are fetched one request per vacancy; hirify rate-limits at the
# application level (see HirifyRateLimitedError), so keep the fan-out modest.
_DETAIL_CONCURRENCY = 4
_LISTING_MAX_PAGES = 50

_DETAIL_URL_RE = re.compile(r"/jobs/[a-z0-9-]*\d[a-z0-9-]*", re.IGNORECASE)
_NUXT_CONFIG_RE = re.compile(
    r"window\.__NUXT__\.config=\{public:\{apiBase:\"([^\"]+)\"",
    re.IGNORECASE,
)
_QUERY_BY_PATH: dict[str, dict[str, str]] = {
    "/jobs-in-russia": {"countries": "russia", "regions": "russia"},
    "/jobs-in-europe": {"regions": "europe", "excluded_countries": "russia,belarus"},
}


class _HirifyRateGate:
    """One-request-per-second gate with an authoritative server cooldown."""

    def __init__(self, interval_seconds: float = 1.0) -> None:
        self._interval = interval_seconds
        self._next_at = 0.0
        self._cooldown_until = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            ready_at = max(now, self._next_at, self._cooldown_until)
            self._next_at = ready_at + self._interval
        wait_seconds = max(0.0, ready_at - now)
        remaining = remaining_source_seconds()
        if remaining is not None and wait_seconds > remaining:
            raise HirifyRateLimitedError(
                f"Hirify flood wait {wait_seconds:.1f}s exceeds remaining source budget",
                retry_after_seconds=wait_seconds,
            )
        await sleep_with_source_deadline(wait_seconds)

    def record_rate_limit(self, wait_seconds: float) -> None:
        now = time.monotonic()
        self._cooldown_until = max(self._cooldown_until, now + max(0.0, wait_seconds))
        self._interval = min(2.0, max(self._interval, 1.0) * 1.25)

    def record_success(self) -> None:
        self._interval = max(1.0, self._interval * 0.98)


class HirifyRateLimitedError(RuntimeError):
    """Raised when Hirify returns its application-level rate-limit response."""

    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def _retry_after_seconds(response_or_headers: Any, body: str = "") -> float:
    """Read the most specific Hirify flood-wait signal available."""
    headers = getattr(response_or_headers, "headers", response_or_headers)
    try:
        normalized = {str(key).casefold(): str(value) for key, value in headers.items()}
    except AttributeError:
        normalized = {}
    retry_after = parse_retry_after(normalized.get("retry-after"), cap_seconds=3600.0)
    if retry_after is not None:
        return retry_after

    reset = normalized.get("x-ratelimit-reset")
    if reset:
        try:
            reset_value = float(reset)
        except ValueError:
            reset_value = 0.0
        if reset_value > 0:
            if reset_value > time.time():
                return min(3600.0, reset_value - time.time())
            if reset_value <= 3600.0:
                return reset_value

    text = body
    if not text and isinstance(getattr(response_or_headers, "text", None), str):
        text = response_or_headers.text
    try:
        payload = json.loads(text) if text.strip() else None
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        for key in ("retry_after", "retryAfter", "wait", "wait_seconds", "seconds"):
            value = payload.get(key)
            try:
                if value is not None and float(value) >= 0:
                    return float(value)
            except (TypeError, ValueError):
                continue
    match = re.search(
        r"(?:retry|wait|try again|available).{0,80}?([0-9]+(?:\.[0-9]+)?)\s*(?:seconds?|secs?|s)\b",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        return float(match.group(1))
    return 5.0


def _extract_detail_urls(html: str, base_url: str, *, limit: int) -> list[str]:
    return extract_urls_with_limit(html, _DETAIL_URL_RE, base_url, limit)


def _listing_card_rows(html: str, base_url: str, *, limit: int) -> list[dict[str, Any]]:
    """Extract SSR cards before falling back to URL-only discovery.

    The first Hirify page is server-rendered and contains useful card metadata,
    even when the JSON endpoint is rate-limited. It is not the vacancy body;
    detail extraction still owns the full posting.
    """
    tree = LexborHTMLParser(html or "")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in tree.css(".vacancy-card[data-vacancy-id]"):
        anchor = card.css_first("a.vacancy-card-link") or card.css_first('a[href*="/jobs/"]')
        if anchor is None:
            continue
        href = str(anchor.attributes.get("href") or "").strip()
        absolute = urljoin(base_url, href).split("?", 1)[0]
        if not _DETAIL_URL_RE.search(urlparse(absolute).path):
            continue
        vacancy_id = str(card.attributes.get("data-vacancy-id") or "").strip()
        vacancy_id = vacancy_id or (_id_from_detail_url(absolute) or "")
        if not vacancy_id or vacancy_id in seen:
            continue
        seen.add(vacancy_id)
        title_node = card.css_first(".title") or card.css_first("h3") or card.css_first("h2")
        company_node = card.css_first(".company")
        tags = [
            " ".join(node.text(separator=" ", strip=True).split())
            for node in card.css(".tag")
            if node.text(separator=" ", strip=True).strip()
        ]
        row: dict[str, Any] = {
            "id": vacancy_id,
            "slug": urlparse(absolute).path.rstrip("/").rsplit("/", 1)[-1],
            "title": (
                " ".join(title_node.text(separator=" ", strip=True).split())
                if title_node is not None
                else ""
            ),
            "company_title": (
                " ".join(company_node.text(separator=" ", strip=True).split())
                if company_node is not None
                else ""
            ),
            "listing_card_tags": tags,
            "listing_card_text": " ".join(card.text(separator=" ", strip=True).split()),
        }
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


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
    raw_url = row.get("url") or row.get("job_url") or row.get("vacancy_url")
    if isinstance(raw_url, str) and raw_url.strip():
        candidate = urljoin("https://hirify.me", raw_url.strip()).split("?", 1)[0]
        if _DETAIL_URL_RE.search(urlparse(candidate).path):
            return candidate
    slug = row.get("slug")
    vacancy_id = row.get("id")
    if isinstance(slug, str) and slug.strip():
        return f"https://hirify.me/jobs/{slug.strip()}"
    if vacancy_id is not None:
        return f"https://hirify.me/jobs/{vacancy_id}"
    return None


def _row_identity(row: dict[str, Any]) -> str:
    value = row.get("id") or row.get("vacancy_id")
    if value is not None and str(value).strip():
        return f"id:{value}"
    url = _detail_url(row)
    return f"url:{url or row.get('slug') or repr(sorted(row.items()))}"


def _urls_from_rows(
    rows: list[dict[str, Any]],
    base_url: str,
    *,
    limit: int,
    seen: set[str] | None = None,
) -> list[str]:
    urls: list[str] = []
    seen_urls = seen if seen is not None else set()
    for row in rows:
        raw_url = _detail_url(row)
        if not raw_url:
            continue
        absolute = urljoin(base_url, raw_url)
        if absolute in seen_urls or not _DETAIL_URL_RE.search(urlparse(absolute).path):
            continue
        seen_urls.add(absolute)
        urls.append(absolute)
        if len(urls) >= limit:
            break
    return urls


def _payload_int(payload: dict[str, Any], *keys: str) -> int | None:
    containers = [payload]
    for nested_key in ("pagination", "meta", "page"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            containers.append(nested)
    for container in containers:
        for key in keys:
            value = container.get(key)
            if not isinstance(value, (int, float, str)):
                continue
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            if number >= 0:
                return number
    return None


def _next_api_request(
    payload: dict[str, Any],
    *,
    current_url: str,
    api_url: str,
    query: dict[str, str],
    page: int,
) -> tuple[str, dict[str, str] | None, int] | None:
    """Resolve Hirify's page, next-link, and cursor pagination shapes."""
    containers = [payload]
    for nested_key in ("pagination", "meta", "links", "page"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            containers.append(nested)

    cursor_keys = {"cursor", "next_cursor", "nextCursor", "after", "pageToken", "page_token"}
    symbolic_next = False
    for container in containers:
        for key in ("next_page_url", "nextPageUrl", "next_url", "nextUrl", "next"):
            value = container.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            value = value.strip()
            if value.startswith(("http://", "https://", "/", "?")):
                return urljoin(current_url, value), None, page + 1
            if key in cursor_keys:
                request_key = "cursor" if key in {"next_cursor", "nextCursor"} else key
                return api_url, {**query, request_key: value}, page
            # Test doubles and some Laravel responses use a symbolic
            # next_page_url; page metadata below remains authoritative.
            symbolic_next = True
            break
        for key in cursor_keys:
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                cursor_request_key = "cursor" if key in {"next_cursor", "nextCursor"} else key
                return api_url, {**query, cursor_request_key: value.strip()}, page

    current = _payload_int(payload, "current_page", "currentPage", "page")
    last = _payload_int(payload, "last_page", "lastPage", "total_pages", "totalPages")
    if current is not None and last is not None and current < last:
        next_page = current + 1
        return api_url, {**query, "page": str(next_page)}, next_page
    if symbolic_next:
        next_page = page + 1
        return api_url, {**query, "page": str(next_page)}, next_page
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

    def __init__(self) -> None:
        self._rate_gate = _HirifyRateGate()
        self._api_request_lock = asyncio.Lock()
        self.last_retry_after_seconds: float | None = None

    async def _api_get(
        self,
        client: Any,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str],
        retry_rate_limit: bool = True,
    ) -> Any:
        # Keep the endpoint-wide flood window ordered. Four concurrent callers
        # could otherwise already be queued before the first 429 is observed.
        async with self._api_request_lock:
            return await self._api_get_locked(
                client,
                url,
                params=params,
                headers=headers,
                retry_rate_limit=retry_rate_limit,
            )

    async def _api_get_locked(
        self,
        client: Any,
        url: str,
        *,
        params: dict[str, str] | None,
        headers: dict[str, str],
        retry_rate_limit: bool = True,
    ) -> Any:
        last_wait = 0.0
        # The default career client wraps httpx and raises on final 429. Use
        # its raw client here so this parser owns the server-provided wait and
        # never sleeps twice for the same response.
        request_client = getattr(client, "_client", client)
        attempts = 2 if retry_rate_limit else 1
        for attempt in range(attempts):
            await self._rate_gate.acquire()
            response = await fetch_with_retry(
                request_client,
                url,
                params=params,
                headers=headers,
                follow_redirects=True,
                max_attempts=1,
            )
            if response.status_code != 429:
                self._rate_gate.record_success()
                return response
            last_wait = _retry_after_seconds(response)
            if not retry_rate_limit:
                raise HirifyRateLimitedError(
                    f"429 Too many requests from hirify api; wait {last_wait:.1f}s",
                    retry_after_seconds=last_wait,
                )
            self._rate_gate.record_rate_limit(last_wait)
            logger.warning(
                "hirify_rate_limit_wait",
                url=url,
                wait_seconds=last_wait,
                attempt=attempt + 1,
            )
        raise HirifyRateLimitedError(
            f"429 Too many requests from hirify api; wait {last_wait:.1f}s",
            retry_after_seconds=last_wait,
        )

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
        return SiteRuntimeDefaults(
            include_if_detail_page=True,
            render=True,
            wait="networkidle",
            extra={"source_hard_deadline_seconds": 600.0},
        )

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
        rows = await self._fetch_listing_rows(spec, client, html)
        return _urls_from_rows(rows, spec.url, limit=spec.limit or 50)

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
        limit = spec.limit or 50
        target_url = f"{api_url}?{urlencode({**query, 'page': '1'})}"

        async def _fetch_once() -> dict[str, Any]:
            await self._rate_gate.acquire()
            script = """
                async (targetUrl) => {
                    const response = await fetch(targetUrl, {
                        credentials: "include",
                        headers: { accept: "application/json, text/plain, */*" },
                    });
                    return {
                        status: response.status,
                        headers: Object.fromEntries(response.headers.entries()),
                        text: await response.text(),
                    };
                }
            """
            return cast("dict[str, Any]", await page.evaluate(script, target_url))

        async def _fetch_with_flood_wait() -> dict[str, Any]:
            result: dict[str, Any] = {}
            for attempt in range(2):
                result = await _fetch_once()
                if int(result.get("status") or 0) != 429:
                    self._rate_gate.record_success()
                    return result
                wait_seconds = _retry_after_seconds(
                    result.get("headers"), str(result.get("text") or "")
                )
                self._rate_gate.record_rate_limit(wait_seconds)
                logger.warning(
                    "hirify_browser_rate_limit_wait",
                    wait_seconds=wait_seconds,
                    attempt=attempt + 1,
                )
            raise HirifyRateLimitedError(
                "429 Too many requests from hirify browser api",
                retry_after_seconds=wait_seconds,
            )

        urls: list[str] = []
        seen: set[str] = set()
        seen_requests: set[str] = set()
        page_number = 1
        for _ in range(_LISTING_MAX_PAGES):
            if target_url in seen_requests:
                break
            seen_requests.add(target_url)
            result = await _fetch_with_flood_wait()
            if int(result.get("status") or 0) >= 400:
                result_text = str(result.get("text") or "")
                if is_challenge_response(result_text):
                    raise_if_browser_challenge(
                        result_text,
                        url=str(result.get("url") or target_url),
                    )
                raise RuntimeError(
                    f"browser api fetch failed with status {result.get('status')}: {result_text[:200]}"
                )
            payload = json.loads(str(result.get("text") or "{}"))
            if not isinstance(payload, dict):
                break
            data = payload.get("data")
            page_rows = (
                [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []
            )
            urls.extend(_urls_from_rows(page_rows, spec.url, limit=limit - len(urls), seen=seen))
            if len(urls) >= limit or not page_rows:
                break
            next_request = _next_api_request(
                payload,
                current_url=target_url,
                api_url=api_url,
                query=query,
                page=page_number,
            )
            if next_request is None:
                break
            next_url, next_params, page_number = next_request
            target_url = next_url if next_params is None else f"{next_url}?{urlencode(next_params)}"
        return urls

    async def _fetch_listing_rows(
        self,
        spec: CareerSiteSpec,
        client: Any,
        html: str,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen_rows: set[str] = set()
        seen_requests: set[str] = set()
        api_url = self._api_url(html)
        query = self._query_for_spec(spec)
        keywords = keywords_from_spec(spec) if query.get("search") else []
        request_url = api_url
        page = 1
        limit = spec.limit or 50
        params: dict[str, str] | None = {**query, "page": str(page)}
        for _ in range(_LISTING_MAX_PAGES):
            request_key = f"{request_url}?{sorted((params or {}).items())}"
            if request_key in seen_requests:
                break
            seen_requests.add(request_key)
            response = await self._api_get(
                client,
                request_url,
                params=params,
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Referer": spec.url,
                    "Origin": "https://hirify.me",
                },
            )
            response_text = str(getattr(response, "text", "") or "")
            if is_challenge_response(response_text):
                raise_if_browser_challenge(
                    response_text,
                    url=str(getattr(response, "url", request_url) or request_url),
                )
            if response.status_code == 429:
                raise HirifyRateLimitedError("429 Too many requests from hirify api")
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            page_rows = (
                [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []
            )
            for row in page_rows:
                identity = _row_identity(row)
                if identity in seen_rows:
                    continue
                seen_rows.add(identity)
                if keywords and not listing_matches_keywords(
                    str(row.get("title") or row.get("original_title") or ""),
                    str(row.get("listing_card_text") or row.get("tldr") or ""),
                    keywords,
                ):
                    continue
                rows.append(row)
                if len(rows) >= limit:
                    return rows[:limit]
            if not page_rows:
                break
            next_request = _next_api_request(
                payload,
                current_url=str(getattr(response, "url", request_url) or request_url),
                api_url=api_url,
                query=query,
                page=page,
            )
            if next_request is None:
                break
            request_url, params, page = next_request
        return rows[:limit]

    async def _fetch_detail_body(
        self,
        client: Any,
        html: str,
        vacancy_id: Any,
        referer: str,
    ) -> dict[str, Any] | None:
        """Fetch one vacancy's body. Returns None so a single failure is skipped."""
        try:
            response = await self._api_get(
                client,
                f"{self._api_url(html)}/{vacancy_id}",
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Referer": referer,
                    "Origin": "https://hirify.me",
                },
                retry_rate_limit=False,
            )
            response_text = str(getattr(response, "text", "") or "")
            if is_challenge_response(response_text):
                raise_if_browser_challenge(
                    response_text,
                    url=str(getattr(response, "url", referer) or referer),
                )
            if response.status_code == 429:
                raise HirifyRateLimitedError("429 Too many requests from hirify detail api")
            response.raise_for_status()
            payload = response.json()
        except (BrowserChallengeError, HirifyRateLimitedError):
            raise
        except Exception as exc:  # noqa: BLE001 - one bad vacancy must not sink the run
            logger.debug("hirify_detail_fetch_failed", vacancy_id=vacancy_id, error=str(exc))
            return None
        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            payload = payload["data"]
        return payload if isinstance(payload, dict) else None

    async def _fetch_details_via_browser(
        self,
        spec: CareerSiteSpec,
        html: str,
        rows: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Retry rate-limited details through the hydrated browser session."""
        bypass_strategy = spec.monitor_config.get("_bypass_strategy")
        browser_config = resolve_browser_config(spec, bypass_strategy)
        result_by_id: dict[str, dict[str, Any]] = {}
        async with open_page(browser_config, bypass_strategy=bypass_strategy) as page:
            await navigate(page, spec.url, browser_config)
            page_url = str(getattr(page, "url", spec.url) or spec.url)
            browser_html = await page.content()
            if is_challenge_response(browser_html):
                raise_if_browser_challenge(browser_html, url=page_url)
            api_url = self._api_url(browser_html or html)
            targets = [
                (str(row.get("id")), f"{api_url}/{row.get('id')}")
                for row in rows
                if row.get("id") is not None
            ]
            script = """
                async (targets) => Promise.all(targets.map(async ([id, url]) => {
                    const response = await fetch(url, {
                        credentials: "include",
                        headers: { accept: "application/json, text/plain, */*" },
                    });
                    return {
                        id,
                        url,
                        status: response.status,
                        headers: Object.fromEntries(response.headers.entries()),
                        text: await response.text(),
                    };
                }))
            """
            # One browser request consumes one gate slot. Promise.all over a
            # batch would defeat the flood-wait protection with a burst.
            for offset in range(len(targets)):
                batch = targets[offset : offset + 1]
                for attempt in range(2):
                    await self._rate_gate.acquire()
                    responses = await page.evaluate(script, batch)
                    rate_limited = (
                        [
                            result
                            for result in responses
                            if isinstance(result, dict) and int(result.get("status") or 0) == 429
                        ]
                        if isinstance(responses, list)
                        else []
                    )
                    if not rate_limited:
                        self._rate_gate.record_success()
                        break
                    wait_seconds = max(
                        _retry_after_seconds(result.get("headers"), str(result.get("text") or ""))
                        for result in rate_limited
                    )
                    self.last_retry_after_seconds = max(
                        self.last_retry_after_seconds or 0.0,
                        wait_seconds,
                    )
                    self._rate_gate.record_rate_limit(wait_seconds)
                    logger.warning(
                        "hirify_browser_detail_rate_limit_wait",
                        wait_seconds=wait_seconds,
                        attempt=attempt + 1,
                    )
                else:
                    responses = []
                if not isinstance(responses, list):
                    continue
                for result in responses:
                    if not isinstance(result, dict):
                        continue
                    result_text = str(result.get("text") or "")
                    if is_challenge_response(result_text):
                        raise_if_browser_challenge(
                            result_text,
                            url=str(result.get("url") or spec.url),
                        )
                    if int(result.get("status") or 0) != 200:
                        continue
                    try:
                        payload = json.loads(str(result.get("text") or "{}"))
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
                        payload = payload["data"]
                    if isinstance(payload, dict):
                        result_by_id[str(result.get("id"))] = payload
        return result_by_id

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

        # `text` is the posting itself. `tldr` is hirify's own summary. When
        # the detail call 429s, keep the listing card (title/company/snippet)
        # instead of dropping the vacancy or waiting Retry-After.
        body = _html_to_text(str(merged.get("text") or ""))
        if not body:
            body = str(merged.get("clear_text") or "").strip() or str(merged.get("tldr") or "")
        if not body:
            body = str(merged.get("listing_card_text") or "").strip()
        company = str(merged.get("company_title") or "").strip()
        sections = [title, company]
        if body and " ".join(body.split()).casefold() != " ".join(title.split()).casefold():
            sections.append(body)

        work_format = _names(merged.get("work_format"))
        regions = _names(merged.get("regions"), "name", "name_en")
        metadata: dict[str, Any] = {
            "source_family": SourceFamily.ATS_API.value,
            "observation_kind": ObservationKind.VACANCY_DETAIL.value,
            "transport": AcquisitionTransport.HTTP.value,
            "adapter": "hirify-api",
            "parser": "hirify-api",
            "parser_version": "hirify-api-v1",
            "listing_card_extracted": bool(merged.get("listing_card_text")),
            "listing_card_tags": merged.get("listing_card_tags") or None,
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
        self.last_retry_after_seconds = None
        try:
            response = await safe_fetch(client, spec.url)
            html = str(response.text)
            if is_challenge_response(html):
                raise_if_browser_challenge(html, url=str(getattr(response, "url", spec.url)))
        except BrowserChallengeError:
            raise
        except Exception as exc:  # noqa: BLE001 - API has a documented default host
            logger.info("hirify_page_fetch_failed", url=spec.url, error=str(exc))
            html = ""

        card_rows = _listing_card_rows(html, spec.url, limit=spec.limit or 50)
        rate_limited = False
        discovery_attempted = False
        search_requested = bool(self._query_for_spec(spec).get("search"))
        try:
            rows = await self._fetch_listing_rows(spec, client, html)
        except BrowserChallengeError:
            raise
        except HirifyRateLimitedError as exc:
            logger.warning("hirify_api_rate_limited", url=spec.url)
            self.last_retry_after_seconds = max(
                self.last_retry_after_seconds or 0.0,
                float(exc.retry_after_seconds or 0.0),
            )
            # Query parameters do not filter Hirify's SSR viewport; only reuse
            # those cards for an unfiltered feed, never as false search hits.
            rows = [] if search_requested else card_rows
            rate_limited = True
        except Exception as exc:  # noqa: BLE001
            logger.info("hirify_api_listing_failed", url=spec.url, error=str(exc))
            rows = card_rows

        limit = spec.limit or 50
        if rate_limited and len(rows) < limit:
            # The SSR feed is only the first viewport. If the API was throttled,
            # let the hydrated browser discover the remaining cursor/page URLs.
            discovery_attempted = True
            try:
                discovered_urls = await self.discover(spec, client)
            except BrowserChallengeError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.info("hirify_discover_after_rate_limit_failed", url=spec.url, error=str(exc))
            else:
                seen_ids = {str(row.get("id")) for row in rows if row.get("id") is not None}
                for url in discovered_urls:
                    vacancy_id = _id_from_detail_url(url)
                    if vacancy_id is None or vacancy_id in seen_ids:
                        continue
                    rows.append({"id": vacancy_id, "slug": urlparse(url).path.rsplit("/", 1)[-1]})
                    seen_ids.add(vacancy_id)
                    if len(rows) >= limit:
                        break

        if not rows and not discovery_attempted:
            # Listing API unavailable. Discovery still has HTML-link and browser
            # paths, and the per-vacancy endpoint is separate, so an id is often
            # enough to recover the body without the listing.
            try:
                urls = await self.discover(spec, client)
            except BrowserChallengeError:
                raise
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

        rows = rows[:limit]
        semaphore = asyncio.Semaphore(_DETAIL_CONCURRENCY)
        stop_details = False

        async def _detail(row: dict[str, Any]) -> dict[str, Any] | None:
            nonlocal stop_details
            vacancy_id = row.get("id")
            if vacancy_id is None or stop_details:
                return None
            async with semaphore:
                if stop_details:
                    return None
                try:
                    return await self._fetch_detail_body(
                        client, html, vacancy_id, _detail_url(row) or spec.url
                    )
                except HirifyRateLimitedError:
                    stop_details = True
                    raise

        details_with_errors = await asyncio.gather(
            *(_detail(row) for row in rows), return_exceptions=True
        )
        details: list[dict[str, Any] | None] = []
        rate_limited_rows: list[dict[str, Any]] = []
        for row, result in zip(rows, details_with_errors, strict=True):
            if isinstance(result, HirifyRateLimitedError):
                rate_limited_rows.append(row)
                details.append(None)
            elif isinstance(result, BrowserChallengeError):
                raise result
            elif isinstance(result, BaseException):
                logger.debug(
                    "hirify_detail_fetch_failed",
                    vacancy_id=row.get("id"),
                    error=str(result),
                )
                details.append(None)
            else:
                details.append(result)

        if rate_limited_rows:
            logger.info(
                "hirify_detail_listing_fallback",
                url=spec.url,
                rate_limited_details=len(rate_limited_rows),
            )
        if rate_limited:
            logger.info(
                "hirify_listing_card_fallback",
                url=spec.url,
                cards=len(card_rows),
                rate_limited_details=len(rate_limited_rows),
            )

        source_name = spec.source_name or source_spec_name(spec)
        keywords = keywords_from_spec(spec) if search_requested else []
        emitted = 0
        for row, detail in zip(rows, details, strict=True):
            item = self._to_raw_item(row, detail, source_name)
            if item is None:
                continue
            title = str(item.text or "").split("\n", 1)[0]
            if keywords and not listing_matches_keywords(title, "", keywords):
                continue
            emitted += 1
            yield item
        logger.info("hirify_api_parsed", url=spec.url, rows=len(rows), emitted=emitted)

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        response = await safe_fetch(client, spec.url)
        current_url = str(response.url)
        if _DETAIL_URL_RE.search(current_url):
            return [current_url.split("?", 1)[0]]
        html = str(response.text)
        if is_challenge_response(html):
            raise_if_browser_challenge(html, url=current_url)
        # When a keyword search is requested, the links baked into the page HTML
        # are the default unfiltered feed - going straight to the API is the only
        # way the `search` filter is actually applied. Only take the HTML links
        # directly when there is no search query.
        has_search = bool(self._query_for_spec(spec).get("search"))
        if not has_search:
            direct_urls = _extract_detail_urls(html, current_url, limit=spec.limit or 50)
            if direct_urls:
                return direct_urls

        try:
            api_urls = await self._discover_via_api(spec, client, html)
            if api_urls:
                return api_urls
        except HirifyRateLimitedError as exc:
            logger.warning("hirify_api_rate_limited", url=spec.url)
            self.last_retry_after_seconds = max(
                self.last_retry_after_seconds or 0.0,
                float(exc.retry_after_seconds or 0.0),
            )
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            logger.debug("hirify_api_invalid_payload", url=spec.url, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.debug("hirify_api_discover_failed", url=spec.url, error=str(exc))

        bypass_strategy = spec.monitor_config.get("_bypass_strategy")
        browser_config = resolve_browser_config(spec, bypass_strategy)
        async with open_page(browser_config, bypass_strategy=bypass_strategy) as page:
            await navigate(page, spec.url, browser_config)
            page_url = getattr(page, "url", spec.url) or spec.url
            browser_html = await page.content()
            if is_challenge_response(browser_html):
                raise_if_browser_challenge(browser_html, url=str(page_url))
            if has_search:
                for selector in (
                    "input.search-input",
                    'input[type="search"]',
                    'input[placeholder*="ваканс"]',
                ):
                    try:
                        locator = page.locator(selector).first
                        if await locator.count() == 0:
                            continue
                        await locator.fill(str(self._query_for_spec(spec)["search"]))
                        await locator.press("Enter")
                        wait_for_load_state = getattr(page, "wait_for_load_state", None)
                        if callable(wait_for_load_state):
                            await wait_for_load_state(
                                "domcontentloaded",
                                timeout=int(browser_config.get("timeout", 30_000)),
                            )
                        browser_html = await page.content()
                        if is_challenge_response(browser_html):
                            raise_if_browser_challenge(browser_html, url=str(page_url))
                        break
                    except BrowserChallengeError:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        logger.debug(
                            "hirify_browser_search_box_failed",
                            selector=selector,
                            error=str(exc),
                        )
            if _DETAIL_URL_RE.search(urlparse(page_url).path):
                return [page_url.split("?", 1)[0]]
            try:
                browser_api_urls = await self._discover_via_browser_api(
                    page, spec, browser_html or html
                )
                if browser_api_urls:
                    return browser_api_urls
            except HirifyRateLimitedError as exc:
                logger.warning("hirify_browser_api_rate_limited", url=spec.url)
                self.last_retry_after_seconds = max(
                    self.last_retry_after_seconds or 0.0,
                    float(exc.retry_after_seconds or 0.0),
                )
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                logger.debug("hirify_browser_api_invalid_payload", url=spec.url, error=str(exc))
            except Exception as exc:  # noqa: BLE001
                logger.debug("hirify_browser_api_failed", url=spec.url, error=str(exc))
            limit = spec.limit or 50
            return await browser_scroll_collect_urls(
                page,
                page_url,
                _DETAIL_URL_RE,
                limit=limit,
                scroll_loops=min(_LISTING_MAX_PAGES, max(4, (limit + 14) // 15 + 2)),
                pause_sec=0.75,
                scroll_px=3000,
                stale_rounds=4,
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
