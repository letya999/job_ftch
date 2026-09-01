"""Site parser for getmatch.ru IT vacancies.

Getmatch listing pages are a Next.js SPA. Public listing APIs return
``Login required`` (auth wall). Vacancy discovery therefore uses the public
sitemap of detail URLs. Detail pages are server-rendered HTML with stable
selectors (h1, company, salary, locations, description).

Fetcher stays thin: this module only extracts candidates/drafts from supplied
HTML/API/sitemap artifacts. Challenge/auth/layout outcomes are raised as
explainable local errors (or mapped onto existing challenge exceptions).
"""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, cast
from urllib.parse import parse_qsl, urljoin, urlparse, urlunparse

import structlog
from selectolax.parser import HTMLParser

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.monitors.shared import BrowserChallengeError
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    normalize_search_keywords,
    with_query_params,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

logger = structlog.get_logger(__name__)

_DOMAIN_PATTERN = r"^https?://(?:www\.)?getmatch\.ru(?:/|$)"
_URL_FILTER = r"getmatch\.ru/vacancies/\d+(?:-[a-z0-9-]+)?"
_DETAIL_PATH_RE = re.compile(
    r"/vacancies/(\d+)(?:-([a-z0-9][a-z0-9-]*))?",
    re.IGNORECASE,
)
_DETAIL_HREF_RE = re.compile(
    r"(?:https?://(?:www\.)?getmatch\.ru)?/vacancies/(\d+)(?:-([a-z0-9][a-z0-9-]*))?",
    re.IGNORECASE,
)
_SITEMAP_LOC_RE = re.compile(
    r"<loc>\s*(https?://(?:www\.)?getmatch\.ru/vacancies/\d+(?:-[^<\s]+)?)\s*</loc>",
    re.IGNORECASE,
)
_PUBLICATION_DATE_RE = re.compile(
    r"Дата публикации:\s*(\d{1,2})\.(\d{1,2})\.(\d{4})",
    re.IGNORECASE,
)
_EMPTY_MARKERS: tuple[str, ...] = (
    "вакансий не найдено",
    "вакансии не найдены",
    "ничего не найдено",
    "no vacancies found",
    "no open positions",
    "нет подходящих вакансий",
)
_AUTH_MARKERS: tuple[str, ...] = (
    "login required",
    "authorization required",
    "требуется авторизация",
    "войдите, чтобы продолжить",
    "sign in to continue",
)
_CHALLENGE_STRONG_MARKERS: tuple[str, ...] = (
    "checking your browser",
    "just a moment",
    "performing security verification",
    "cf-chl",
    "cf-turnstile",
    "smartcaptcha-container",
    "showcaptcha",
    "hcaptcha",
    "g-recaptcha",
)
_LISTING_SHELL_MARKERS: tuple[str, ...] = (
    "pageTitle",
    "Вакансии",
    "seoFilters",
    "b-filter",
    "Изменить фильтры",
)
_DETAIL_SHELL_MARKERS: tuple[str, ...] = (
    "b-vacancy",
    "b-vacancy-description",
    "b-vacancy-header",
    "b-location",
)
_DEFAULT_SITEMAP_URL = "https://getmatch.ru/sitemap.xml"
_DEFAULT_BOARD_URL = "https://getmatch.ru/vacancies"

GetmatchFailureKind = Literal[
    "empty_result",
    "layout_changed",
    "challenge_required",
    "auth_wall",
    "parser_error",
    "deadline",
]


class GetmatchPageKind(StrEnum):
    LISTING = "listing"
    DETAIL = "detail"
    EMPTY = "empty_result"
    CHALLENGE = "challenge_required"
    AUTH_WALL = "auth_wall"
    LAYOUT_CHANGED = "layout_changed"
    UNKNOWN = "unknown"


class GetmatchIngestError(RuntimeError):
    """Explainable Getmatch ingest failure for source health / diagnostics.

    SiteParser protocol still yields only ``RawItem`` streams. Terminal or
    degraded reasons therefore ride on this exception's ``kind`` (and the
    ``"{kind}: {message}"`` string form) so existing ``SourceFetchResult.error``
    / ``SourceHealth.last_error`` paths can surface allowlisted public codes
    without a broader parser-result schema.
    """

    def __init__(
        self,
        kind: GetmatchFailureKind,
        message: str,
        *,
        url: str | None = None,
    ) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.url = url
        self.public_reason = message


_PUBLIC_FAILURE_CODES: frozenset[str] = frozenset(
    {
        "empty_result",
        "layout_changed",
        "challenge_required",
        "auth_wall",
        "parser_error",
        "deadline",
    }
)


def public_failure_code_for(
    value: GetmatchFailureKind | GetmatchPageKind | GetmatchIngestError | str | None,
) -> GetmatchFailureKind | None:
    """Map a Getmatch classification/error to a public-safe failure code.

    Returns one of the allowlisted diagnostics codes used by public source
    health (``empty_result``, ``layout_changed``, ``challenge_required``,
    ``auth_wall``, ``parser_error``, ``deadline``), or ``None`` for non-terminal
    page kinds such as listing/detail.
    """
    if value is None:
        return None
    if isinstance(value, GetmatchIngestError):
        return value.kind
    raw = getattr(value, "value", value)
    text = str(raw).strip().casefold().replace("-", "_").replace(" ", "_")
    if text in _PUBLIC_FAILURE_CODES:
        return cast("GetmatchFailureKind", text)
    return None


def canonicalize_vacancy_url(url: str) -> str | None:
    """Return a stable absolute detail URL without tracking query params."""
    if not url or not isinstance(url, str):
        return None
    raw = html.unescape(url.strip())
    if not raw:
        return None
    absolute = urljoin(_DEFAULT_BOARD_URL + "/", raw)
    parsed = urlparse(absolute)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host != "getmatch.ru":
        return None
    match = _DETAIL_PATH_RE.search(parsed.path or "")
    if match is None:
        return None
    vacancy_id = match.group(1)
    slug = match.group(2)
    path = f"/vacancies/{vacancy_id}-{slug}" if slug else f"/vacancies/{vacancy_id}"
    return urlunparse(("https", "getmatch.ru", path, "", "", ""))


def vacancy_id_from_url(url: str) -> str | None:
    canonical = canonicalize_vacancy_url(url)
    if canonical is None:
        return None
    match = _DETAIL_PATH_RE.search(urlparse(canonical).path)
    return match.group(1) if match else None


def extract_vacancy_urls_from_html(
    html_text: str,
    base_url: str,
    *,
    limit: int,
    seen: set[str] | None = None,
) -> list[str]:
    """Extract canonical vacancy detail URLs from listing or detail HTML."""
    seen_ids = seen if seen is not None else set()
    urls: list[str] = []
    raw = html.unescape(html_text or "")
    tree = HTMLParser(raw)
    candidates: list[str] = []
    for match in _DETAIL_HREF_RE.finditer(raw):
        vacancy_id, slug = match.group(1), match.group(2)
        path = f"/vacancies/{vacancy_id}-{slug}" if slug else f"/vacancies/{vacancy_id}"
        candidates.append(path)
    for anchor in tree.css("a[href]"):
        href = anchor.attributes.get("href")
        if isinstance(href, str) and href:
            candidates.append(href)

    for candidate in candidates:
        canonical = canonicalize_vacancy_url(urljoin(base_url, candidate))
        if canonical is None:
            continue
        external_id = vacancy_id_from_url(canonical)
        if external_id is None or external_id in seen_ids:
            continue
        seen_ids.add(external_id)
        urls.append(canonical)
        if len(urls) >= limit:
            break
    return urls


def extract_vacancy_urls_from_sitemap(
    xml_text: str,
    *,
    limit: int,
    keywords: Sequence[str] | None = None,
) -> list[str]:
    """Extract newest-looking vacancy URLs from sitemap XML."""
    terms = [
        re.sub(r"[\s_-]+", "-", term.casefold()).strip("-")
        for term in normalize_search_keywords(keywords or ())
    ]
    terms = [term for term in terms if term]
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for match in _SITEMAP_LOC_RE.finditer(xml_text or ""):
        canonical = canonicalize_vacancy_url(match.group(1))
        if canonical is None:
            continue
        external_id = vacancy_id_from_url(canonical)
        if external_id is None or external_id in seen:
            continue
        if terms:
            haystack = re.sub(r"[\s_-]+", "-", canonical.casefold())
            if not any(term in haystack for term in terms):
                continue
        seen.add(external_id)
        scored.append((int(external_id), canonical))
    scored.sort(key=lambda row: row[0], reverse=True)
    return [url for _, url in scored[:limit]]


def classify_getmatch_payload(
    body: str,
    *,
    content_type: str | None = None,
    status_code: int | None = None,
    expected: Literal["listing", "detail", "sitemap", "any"] = "any",
) -> GetmatchPageKind:
    """Classify a Getmatch HTML/JSON/sitemap artifact into an explainable kind."""
    text = body or ""
    lowered = text.casefold()
    ctype = (content_type or "").casefold()

    if status_code in {401, 403}:
        return GetmatchPageKind.AUTH_WALL
    if "application/json" in ctype or text.lstrip().startswith("{"):
        if any(marker in lowered for marker in _AUTH_MARKERS):
            return GetmatchPageKind.AUTH_WALL
        if "login required" in lowered or "not authenticated" in lowered:
            return GetmatchPageKind.AUTH_WALL

    if _looks_like_challenge(text):
        return GetmatchPageKind.CHALLENGE

    if expected == "sitemap" or ("<urlset" in lowered and "<loc>" in lowered):
        if _SITEMAP_LOC_RE.search(text):
            return GetmatchPageKind.LISTING
        if "<urlset" in lowered or "<sitemapindex" in lowered:
            # Sitemap shell present but vacancy locs missing => layout drift.
            return GetmatchPageKind.LAYOUT_CHANGED
        return GetmatchPageKind.LAYOUT_CHANGED

    detail_shell = any(marker.casefold() in lowered for marker in _DETAIL_SHELL_MARKERS)
    listing_shell = any(marker.casefold() in lowered for marker in _LISTING_SHELL_MARKERS)
    has_title = bool(re.search(r"<h1\b", text, flags=re.I))
    has_getmatch_identity = "getmatch" in lowered or "getmatch.ru" in lowered

    if _has_explicit_empty_state(text):
        return GetmatchPageKind.EMPTY

    if expected == "detail":
        if detail_shell or (has_title and has_getmatch_identity):
            return GetmatchPageKind.DETAIL
        if has_getmatch_identity and not detail_shell and not has_title:
            return GetmatchPageKind.LAYOUT_CHANGED
        if not has_getmatch_identity:
            return GetmatchPageKind.LAYOUT_CHANGED
        return GetmatchPageKind.DETAIL

    if expected == "listing":
        if listing_shell or has_getmatch_identity:
            # SPA listing shell without cards is normal; not layout_changed.
            return GetmatchPageKind.LISTING
        return GetmatchPageKind.LAYOUT_CHANGED

    if detail_shell and has_title:
        return GetmatchPageKind.DETAIL
    if listing_shell or has_getmatch_identity:
        return GetmatchPageKind.LISTING
    if has_getmatch_identity:
        return GetmatchPageKind.UNKNOWN
    return GetmatchPageKind.LAYOUT_CHANGED


def _has_explicit_empty_state(text: str) -> bool:
    lowered = text.casefold()
    if not any(marker in lowered for marker in _EMPTY_MARKERS):
        return False
    if re.search(r"(?:data-empty=[\"']true[\"']|class=[\"'][^\"']*empty)", text, flags=re.I):
        return True
    listing_shell = any(marker.casefold() in lowered for marker in _LISTING_SHELL_MARKERS)
    detail_shell = any(marker.casefold() in lowered for marker in _DETAIL_SHELL_MARKERS)
    return not listing_shell and not detail_shell


def _looks_like_challenge(text: str) -> bool:
    """Detect an actual challenge wall, not merely embedded captcha JS."""
    if not text:
        return False
    lowered = text.casefold()
    if any(marker in lowered for marker in _AUTH_MARKERS) and "login required" in lowered:
        return False
    strong = any(marker in lowered for marker in _CHALLENGE_STRONG_MARKERS)
    if not strong:
        return False
    # Real pages embed captcha-api.yandex.ru; require missing vacancy shell.
    has_vacancy_shell = any(
        marker.casefold() in lowered for marker in (*_DETAIL_SHELL_MARKERS, *_LISTING_SHELL_MARKERS)
    )
    has_h1 = bool(re.search(r"<h1\b", text, flags=re.I))
    if has_vacancy_shell or has_h1:
        return False
    visible = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
    visible = re.sub(r"<style\b[^>]*>.*?</style>", " ", visible, flags=re.I | re.S)
    visible = " ".join(re.sub(r"<[^>]+>", " ", visible).split())
    return len(visible) < 400


def _strip_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(html.unescape(value).split())


def _parse_publication_date(text: str) -> datetime | None:
    match = _PUBLICATION_DATE_RE.search(text or "")
    if match is None:
        return None
    day, month, year = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    try:
        return datetime(year, month, day, tzinfo=UTC)
    except ValueError:
        return None


def _work_modes_from_location_text(location_text: str) -> list[str]:
    lowered = location_text.casefold()
    modes: list[str] = []
    if "remote" in lowered or "удал" in lowered:
        modes.append("remote")
    if "hybrid" in lowered or "гибрид" in lowered:
        modes.append("hybrid")
    if "office" in lowered or "офис" in lowered:
        modes.append("office")
    if "relocat" in lowered or "релок" in lowered:
        modes.append("relocation")
    return modes


def item_from_detail_html(
    detail_url: str,
    html_text: str,
    source_name: str,
    board_url: str,
) -> RawItem | None:
    """Build a RawItem draft from a Getmatch detail page HTML artifact."""
    kind = classify_getmatch_payload(html_text, expected="detail")
    if kind is GetmatchPageKind.CHALLENGE:
        raise GetmatchIngestError(
            "challenge_required",
            "detail page is an anti-bot challenge wall",
            url=detail_url,
        )
    if kind is GetmatchPageKind.AUTH_WALL:
        raise GetmatchIngestError(
            "auth_wall",
            "detail page requires authentication",
            url=detail_url,
        )
    if kind is GetmatchPageKind.LAYOUT_CHANGED:
        raise GetmatchIngestError(
            "layout_changed",
            "detail page is missing expected vacancy shell markers",
            url=detail_url,
        )

    canonical = canonicalize_vacancy_url(detail_url) or detail_url
    external_id = vacancy_id_from_url(canonical) or canonical
    tree = HTMLParser(html_text or "")

    title_node = tree.css_first("h1")
    title = _strip_text(title_node.text() if title_node is not None else "")
    if not title:
        title_tag = tree.css_first("title")
        if title_tag is not None:
            raw_title = _strip_text(title_tag.text())
            title = re.sub(
                r"\s*[—-]\s*getmatch.*$",
                "",
                re.sub(r"^Вакансия\s+", "", raw_title, flags=re.I),
                flags=re.I,
            ).strip()

    company_name: str | None = None
    for anchor in tree.css('a[href*="/companies/"]'):
        name = _strip_text(anchor.text())
        if name:
            company_name = name
            break
    if company_name is None:
        for heading in tree.css("h2"):
            text = _strip_text(heading.text())
            if text.casefold().startswith("in"):
                candidate = text[2:].strip()
                if candidate:
                    company_name = candidate
                    break

    salary: str | None = None
    salary_node = tree.css_first("h3")
    if salary_node is not None:
        salary_text = _strip_text(salary_node.text())
        if salary_text and any(ch.isdigit() for ch in salary_text):
            salary = salary_text

    locations: list[str] = []
    location_text = ""
    for sel in (".b-location", ".b-vacancy-locations", ".b-vacancy-locations__group"):
        node = tree.css_first(sel)
        if node is None:
            continue
        location_text = _strip_text(node.text(separator=" "))
        if location_text:
            cleaned = location_text.replace("📍", " ").strip()
            if cleaned:
                locations = [cleaned]
            break

    meta = tree.css_first('meta[name="description"]')
    meta_content = ""
    if meta is not None:
        meta_content = _strip_text(str(meta.attributes.get("content") or ""))
    if not salary and meta_content:
        salary_match = re.search(
            r"Зарплата:\s*([^.]*(?:\d[^.]*)?)",
            meta_content,
            flags=re.I,
        )
        if salary_match:
            salary = _strip_text(salary_match.group(1))
    if company_name is None and meta_content:
        company_match = re.search(
            r"компани[ия]\s+([^,]+)",
            meta_content,
            flags=re.I,
        )
        if company_match:
            company_name = _strip_text(company_match.group(1))

    description = ""
    desc_node = tree.css_first(".b-vacancy-description") or tree.css_first(
        ".b-vacancy-description.markdown"
    )
    if desc_node is not None:
        description = _strip_text(desc_node.text(separator=" "))
    if not description and meta_content:
        description = meta_content

    if not title and not description:
        return None

    archived = bool(
        re.search(
            r"the vacancy has been archived|вакансия (?:в архиве|архивн)|больше не ищет",
            html_text or "",
            flags=re.I,
        )
    )
    work_modes = _work_modes_from_location_text(
        " ".join([location_text, meta_content, html_text[:4000]])
    )
    created_at = _parse_publication_date(meta_content) or _parse_publication_date(html_text)

    text_parts = [title]
    if company_name:
        text_parts.append(company_name)
    if salary:
        text_parts.append(salary)
    text_parts.extend(locations)
    if description:
        text_parts.append(description)

    return build_raw_item(
        source_kind=SourceKind.CAREER_SITE,
        source_name=source_name,
        external_id=external_id,
        url=canonical,
        text="\n".join(part for part in text_parts if part),
        created_at=created_at,
        metadata={
            "board_url": board_url,
            "job_url": canonical,
            "title": title or None,
            "company": company_name,
            "locations": locations or None,
            "work_modes": work_modes or None,
            "base_salary_text": salary,
            "apply_url": canonical,
            "parser": "site_getmatch",
            "adapter": "getmatch",
            "detail_vacancy_confirmed": True,
            "archived": archived or None,
        },
    )


def _keywords_from_spec(spec: CareerSiteSpec) -> list[str]:
    parsed = urlparse(spec.url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for key in ("query", "q", "search", "text", "qs"):
        value = query.get(key)
        if isinstance(value, str) and value.strip():
            # Preserve multi-word roles. Getmatch has no public server-side
            # search endpoint; its sitemap slug is the deterministic local
            # search surface, so splitting on whitespace made `AI engineer`
            # degenerate into the far too broad token `AI`.
            return normalize_search_keywords(re.split(r"\s+OR\s+|\s+or\s+", value))
    roles = getattr(spec, "target_roles", None) or ()
    return normalize_search_keywords(roles)


def _raise_for_kind(kind: GetmatchPageKind, *, url: str, message: str) -> None:
    if kind is GetmatchPageKind.CHALLENGE:
        raise BrowserChallengeError(url=url, challenge_type="getmatch_challenge")
    if kind is GetmatchPageKind.AUTH_WALL:
        raise GetmatchIngestError("auth_wall", message, url=url)
    if kind is GetmatchPageKind.LAYOUT_CHANGED:
        raise GetmatchIngestError("layout_changed", message, url=url)
    if kind is GetmatchPageKind.EMPTY:
        raise GetmatchIngestError("empty_result", message, url=url)


def _response_content_type(response: Any) -> str:
    headers = getattr(response, "headers", None) or {}
    if hasattr(headers, "get"):
        return str(headers.get("content-type") or headers.get("Content-Type") or "")
    return ""


def _response_text(response: Any) -> str:
    return str(getattr(response, "text", "") or "")


async def _get(client: Any, url: str) -> Any:
    return await client.get(url, follow_redirects=True)


def _classify_response(
    response: Any,
    *,
    url: str,
    expected: Literal["listing", "detail", "sitemap", "any"],
) -> tuple[GetmatchPageKind, str]:
    text = _response_text(response)
    kind = classify_getmatch_payload(
        text,
        content_type=_response_content_type(response),
        status_code=getattr(response, "status_code", None),
        expected=expected,
    )
    return kind, text


@register_site_parser(
    "getmatch",
    domain_pattern=_DOMAIN_PATTERN,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:getmatch.ru",
        has_stable_url=True,
        has_publication_time=True,
        has_stable_id=True,
        supports_ordered_head=True,
        has_rss_or_sitemap_dates=False,
        can_detect_freshness_without_snapshot=False,
        ordered_by_newest=True,
        item_level_dates=True,
        requires_full_snapshot=False,
        rationale=(
            "Getmatch public listing APIs require login and listing pages are SPA shells; "
            "the dedicated parser discovers via the public sitemap and extracts server-rendered "
            "detail HTML fields."
        ),
    ),
)
class GetmatchParser:
    """Discover Getmatch vacancies via sitemap/HTML and parse detail drafts."""

    domain_pattern = _DOMAIN_PATTERN
    has_custom_parse = True
    supports_discover = False
    supports_search = True
    search_mode = "combined"
    # Authoritative empty UI is rare; SPA shell without cards is not empty.
    confirmed_empty_on_empty = True
    terminal_on_empty = False

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            url_filter=_URL_FILTER,
            include_if_detail_page=True,
            render=False,
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return "getmatch"

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
        if not (parsed.path or "").startswith("/vacancies"):
            parsed = parsed._replace(path="/vacancies")
        return [
            with_query_params(
                urlunparse(parsed),
                {"query": " OR ".join(terms)},
            )
        ]

    def _limit(self, spec_limit: int | None) -> int:
        if spec_limit is not None:
            return max(1, int(spec_limit))
        manifest_entry = getattr(self, "_manifest_entry", None)
        raw_limit = getattr(manifest_entry, "limit", None) if manifest_entry is not None else None
        if isinstance(raw_limit, (int, float, str)):
            return max(1, int(raw_limit))
        return 50

    def _sitemap_url(self, board_url: str) -> str:
        parsed = urlparse(board_url)
        host = (parsed.hostname or "getmatch.ru").lower()
        if host.startswith("www."):
            host = host[4:]
        scheme = parsed.scheme or "https"
        return f"{scheme}://{host}/sitemap.xml"

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        """Return canonical detail URLs (listing HTML first, then sitemap)."""
        limit = self._limit(spec.limit)
        keywords = _keywords_from_spec(spec)
        board_url = spec.url or _DEFAULT_BOARD_URL
        seen: set[str] = set()
        urls: list[str] = []

        # Detail URL shortcut: operator added a single vacancy.
        direct = canonicalize_vacancy_url(board_url)
        if direct is not None:
            return [direct]

        try:
            listing_response = await _get(client, board_url)
        except Exception as exc:
            logger.debug("getmatch.listing_fetch_failed", url=board_url, error=str(exc))
            listing_response = None

        if listing_response is not None:
            listing_kind, listing_html = _classify_response(
                listing_response,
                url=board_url,
                expected="listing",
            )
            if listing_kind is GetmatchPageKind.CHALLENGE:
                _raise_for_kind(
                    listing_kind,
                    url=board_url,
                    message="listing page is an anti-bot challenge wall",
                )
            if listing_kind is GetmatchPageKind.AUTH_WALL:
                _raise_for_kind(
                    listing_kind,
                    url=board_url,
                    message="listing requires authentication",
                )
            if listing_kind is GetmatchPageKind.EMPTY:
                return []
            if listing_kind is GetmatchPageKind.LAYOUT_CHANGED:
                # Still attempt sitemap before failing hard.
                logger.info("getmatch.listing_layout_changed_fallback_sitemap", url=board_url)
            else:
                urls.extend(
                    extract_vacancy_urls_from_html(
                        listing_html,
                        str(getattr(listing_response, "url", board_url) or board_url),
                        limit=limit,
                        seen=seen,
                    )
                )
                if urls:
                    return urls[:limit]

        sitemap_url = self._sitemap_url(board_url)
        try:
            sitemap_response = await _get(client, sitemap_url)
        except Exception as exc:
            logger.debug("getmatch.sitemap_fetch_failed", url=sitemap_url, error=str(exc))
            if not urls:
                raise GetmatchIngestError(
                    "parser_error",
                    f"failed to fetch sitemap: {exc}",
                    url=sitemap_url,
                ) from exc
            return urls[:limit]

        sitemap_kind, sitemap_text = _classify_response(
            sitemap_response,
            url=sitemap_url,
            expected="sitemap",
        )
        if sitemap_kind is GetmatchPageKind.CHALLENGE:
            _raise_for_kind(
                sitemap_kind,
                url=sitemap_url,
                message="sitemap response is an anti-bot challenge wall",
            )
        if sitemap_kind is GetmatchPageKind.AUTH_WALL:
            _raise_for_kind(
                sitemap_kind,
                url=sitemap_url,
                message="sitemap requires authentication",
            )
        if sitemap_kind is GetmatchPageKind.LAYOUT_CHANGED:
            raise GetmatchIngestError(
                "layout_changed",
                "sitemap is missing vacancy loc entries",
                url=sitemap_url,
            )

        urls.extend(
            extract_vacancy_urls_from_sitemap(
                sitemap_text,
                limit=limit,
                keywords=keywords,
            )
        )
        # Dedupe while preserving order.
        ordered: list[str] = []
        ordered_seen: set[str] = set()
        for url in urls:
            external_id = vacancy_id_from_url(url)
            key = external_id or url
            if key in ordered_seen:
                continue
            ordered_seen.add(key)
            ordered.append(url)
            if len(ordered) >= limit:
                break
        return ordered

    async def parse(
        self,
        spec: CareerSiteSpec,
        client: Any,
    ) -> AsyncIterator[RawItem]:
        limit = self._limit(spec.limit)
        source_name = spec.source_name or "getmatch"
        board_url = spec.url or _DEFAULT_BOARD_URL
        detail_urls = await self.discover(spec, client)
        if not detail_urls:
            # Explicit empty is not a failure; CareerSiteSource maps this via
            # confirmed_empty_on_empty when parse yields nothing.
            return

        emitted = 0
        for detail_url in detail_urls:
            if emitted >= limit:
                break
            try:
                response = await _get(client, detail_url)
            except Exception as exc:
                logger.debug("getmatch.detail_fetch_failed", url=detail_url, error=str(exc))
                continue
            status_code = getattr(response, "status_code", None)
            if isinstance(status_code, int) and status_code >= 400:
                kind = classify_getmatch_payload(
                    _response_text(response),
                    content_type=_response_content_type(response),
                    status_code=status_code,
                    expected="detail",
                )
                if (
                    kind in {GetmatchPageKind.AUTH_WALL, GetmatchPageKind.CHALLENGE}
                    and emitted == 0
                ):
                    _raise_for_kind(
                        kind,
                        url=detail_url,
                        message=f"detail fetch returned {status_code}",
                    )
                logger.debug(
                    "getmatch.detail_http_error",
                    url=detail_url,
                    status_code=status_code,
                )
                continue
            html_text = _response_text(response)
            final_url = str(getattr(response, "url", detail_url) or detail_url)
            try:
                item = item_from_detail_html(
                    final_url,
                    html_text,
                    source_name,
                    board_url,
                )
            except GetmatchIngestError as exc:
                if exc.kind in {"challenge_required", "auth_wall"} and emitted == 0:
                    if exc.kind == "challenge_required":
                        raise BrowserChallengeError(
                            url=final_url,
                            challenge_type="getmatch_challenge",
                        ) from exc
                    raise
                logger.debug(
                    "getmatch.detail_classified_skip",
                    url=final_url,
                    kind=exc.kind,
                )
                continue
            if item is None:
                continue
            if item.metadata.get("archived") is True:
                # Sitemap includes historical vacancies; skip archived by default.
                continue
            yield item
            emitted += 1

        if emitted == 0 and detail_urls:
            # Had candidates but none produced usable drafts.
            raise GetmatchIngestError(
                "layout_changed",
                "detail pages did not yield usable vacancy drafts",
                url=board_url,
            )

    @property
    def __name__(self) -> str:
        return "GetmatchParser"
