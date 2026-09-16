"""Site parser for getmatch.ru IT vacancies.

Getmatch listing pages are a Next.js SPA with no free-text search box.
``?query=`` is stripped client-side, but the parser still puts the terms
there so ``keywords_from_spec`` can read them on the diagnostic ingest path.
Discovery uses public ``/api/offers`` with offset pagination, then sitemap.
Roles are matched locally against listing titles. Detail pages are
server-rendered HTML.

Fetcher stays thin: this module only extracts candidates/drafts from supplied
HTML/API/sitemap artifacts. Challenge/auth/layout outcomes are raised as
explainable local errors (or mapped onto existing challenge exceptions).
"""

from __future__ import annotations

import asyncio
import html
import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, cast
from urllib.parse import parse_qsl, urljoin, urlparse, urlunparse

import structlog
from selectolax.parser import HTMLParser

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.browser_utils import navigate, open_page
from job_ftch.infrastructure.sources.http_retry import fetch_with_retry
from job_ftch.infrastructure.sources.monitors.shared import BrowserChallengeError
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    browser_scroll_collect_urls,
    is_challenge_response,
    keywords_from_spec,
    listing_matches_keywords,
    normalize_search_keywords,
    resolve_browser_config,
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
_DETAIL_URL_RE = re.compile(
    r"^https?://(?:www\.)?getmatch\.ru/vacancies/\d+(?:-[a-z0-9][a-z0-9-]*)?/?$",
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
_OFFERS_API_URL = "https://getmatch.ru/api/offers"
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


def _title_from_offer_row(row: dict[str, Any]) -> str:
    for key in ("position", "title", "name"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return _strip_text(value)
    return ""


def extract_vacancy_urls_from_offers(
    payload: Any,
    *,
    limit: int,
    seen: set[str] | None = None,
    keywords: Sequence[str] = (),
) -> list[str]:
    """Extract canonical vacancy URLs from a public ``/api/offers`` payload."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return []
    if not isinstance(payload, dict):
        return []
    rows = payload.get("offers")
    if not isinstance(rows, list):
        return []
    seen_ids = seen if seen is not None else set()
    urls: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_url = row.get("url")
        if not isinstance(raw_url, str) or not raw_url.strip():
            vacancy_id = row.get("id")
            if isinstance(vacancy_id, (int, str)) and str(vacancy_id).strip():
                raw_url = f"/vacancies/{vacancy_id}"
            else:
                continue
        canonical = canonicalize_vacancy_url(raw_url)
        if canonical is None:
            continue
        external_id = vacancy_id_from_url(canonical)
        if external_id is None or external_id in seen_ids:
            continue
        seen_ids.add(external_id)
        title = _title_from_offer_row(row)
        if keywords and not listing_matches_keywords(title, keywords=keywords):
            continue
        urls.append(canonical)
        if len(urls) >= limit:
            break
    return urls


def _offer_card_from_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize one public API offer into a reusable listing card."""
    raw_url = row.get("url")
    if not isinstance(raw_url, str) or not raw_url.strip():
        vacancy_id = row.get("id")
        if not isinstance(vacancy_id, (int, str)) or not str(vacancy_id).strip():
            return None
        raw_url = f"/vacancies/{vacancy_id}"
    url = canonicalize_vacancy_url(raw_url)
    title = _strip_text(row.get("position") if isinstance(row.get("position"), str) else None)
    if url is None or not title:
        return None

    description_html = row.get("description_html") or row.get("offer_description")
    description = _strip_text(description_html if isinstance(description_html, str) else None)
    company = row.get("company")
    company_name = (
        _strip_text(company.get("name"))
        if isinstance(company, dict) and isinstance(company.get("name"), str)
        else None
    )
    salary = _strip_text(row.get("salary_description"))
    locations: list[str] = []
    work_modes: list[str] = []
    for location in row.get("location_items") or row.get("location_requirements") or ():
        if not isinstance(location, dict):
            continue
        label = _strip_text(location.get("label") or location.get("city"))
        if label and label not in locations:
            locations.append(label)
        mode = _strip_text(location.get("format"))
        if mode and mode not in work_modes:
            work_modes.append(mode)
    skills: list[str] = []
    for skill in row.get("skills_objects") or ():
        value = _strip_text(skill.get("name")) if isinstance(skill, dict) else _strip_text(skill)
        if value and value not in skills:
            skills.append(value)
    card_text = "\n".join(
        part
        for part in (
            title,
            company_name,
            salary,
            ", ".join(locations),
            ", ".join(skills),
            description,
        )
        if part
    )
    return {
        "id": str(row.get("id") or vacancy_id_from_url(url) or "").strip(),
        "url": url,
        "title": title,
        "text": card_text,
        "description": description,
        "description_html": description_html if isinstance(description_html, str) else None,
        "company": company_name,
        "salary": salary,
        "locations": locations,
        "work_modes": work_modes,
        "skills": skills,
        "published_at": row.get("published_at"),
        "language": row.get("language"),
        "offer_type": row.get("offer_type"),
        "is_active": row.get("is_active"),
    }


def _offer_cards_from_payload(
    payload: Any,
    *,
    keywords: Sequence[str] = (),
    seen: set[str] | None = None,
) -> list[dict[str, Any]]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return []
    if not isinstance(payload, dict) or not isinstance(payload.get("offers"), list):
        return []
    seen_ids = seen if seen is not None else set()
    cards: list[dict[str, Any]] = []
    for row in payload["offers"]:
        if not isinstance(row, dict):
            continue
        card = _offer_card_from_row(row)
        if card is None:
            continue
        identity = card["id"] or vacancy_id_from_url(card["url"])
        if not identity or identity in seen_ids:
            continue
        if keywords and not listing_matches_keywords(
            str(card.get("title") or ""),
            str(card.get("text") or ""),
            keywords,
        ):
            continue
        seen_ids.add(identity)
        cards.append(card)
    return cards


def item_from_offer_card(
    card: dict[str, Any],
    source_name: str,
    board_url: str,
) -> RawItem | None:
    """Build a lossless card-level item when detail HTML is unavailable."""
    url = canonicalize_vacancy_url(str(card.get("url") or ""))
    title = _strip_text(card.get("title") if isinstance(card.get("title"), str) else None)
    if url is None or not title:
        return None
    external_id = vacancy_id_from_url(url) or url
    text_parts = [title]
    for key in ("company", "salary"):
        value = _strip_text(card.get(key) if isinstance(card.get(key), str) else None)
        if value:
            text_parts.append(value)
    for key in ("locations", "skills"):
        values = card.get(key)
        if isinstance(values, list) and values:
            text_parts.append(", ".join(str(value) for value in values if value))
    description = _strip_text(
        card.get("description") if isinstance(card.get("description"), str) else None
    )
    if description:
        text_parts.append(description)
    created_at: datetime | None = None
    published_at = card.get("published_at")
    if isinstance(published_at, str):
        try:
            created_at = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        except ValueError:
            created_at = None
    metadata = {
        "board_url": board_url,
        "job_url": url,
        "title": title,
        "company": card.get("company"),
        "company_authoritative": bool(card.get("company")),
        "locations": card.get("locations") or None,
        "work_modes": card.get("work_modes") or None,
        "skills": card.get("skills") or None,
        "base_salary_text": card.get("salary") or None,
        "source_offer_id": external_id,
        "source_offer_type": card.get("offer_type"),
        "source_offer_active": card.get("is_active"),
        "source_description_html": card.get("description_html"),
        "published_at": published_at,
        "parser": "site_getmatch_card",
        "adapter": "getmatch",
        "detail_vacancy_confirmed": False,
        "detail_completeness_reason": "listing_api_card",
    }
    return build_raw_item(
        source_kind=SourceKind.CAREER_SITE,
        source_name=source_name,
        external_id=external_id,
        url=url,
        text="\n".join(text_parts),
        created_at=created_at,
        metadata=metadata,
    )


def _offers_total(payload: Any) -> int | None:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict) or not isinstance(payload.get("meta"), dict):
        return None
    total = payload["meta"].get("total")
    return int(total) if isinstance(total, int) and total >= 0 else None


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
    tree = HTMLParser(text)
    for node in tree.css("script, style"):
        node.decompose()
    visible = " ".join(tree.text(separator=" ", strip=True).split())
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

    canonical = canonicalize_vacancy_url(detail_url)
    if canonical is None:
        return None
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
    desc_node = (
        tree.css_first(".b-vacancy-description")
        or tree.css_first(".b-vacancy-description.markdown")
        or tree.css_first(".markdown")
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
            "company_authoritative": bool(company_name),
            "locations": locations or None,
            "work_modes": work_modes or None,
            "base_salary_text": salary,
            "apply_url": canonical,
            "parser": "site_getmatch",
            "adapter": "getmatch",
            "detail_vacancy_confirmed": bool(desc_node is not None and description),
            "source_description_html": desc_node.html if desc_node is not None else None,
            "detail_completeness_reason": (
                "detail_dom_extracted"
                if desc_node is not None and description
                else "announcement_only"
            ),
            "archived": archived or None,
        },
    )


def _keywords_from_spec(spec: CareerSiteSpec) -> list[str]:
    return keywords_from_spec(spec)


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


def _as_int(value: object, default: int) -> int:
    if isinstance(value, (int, float, str)) and not isinstance(value, bool):
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
    return default


def _listing_cards_from_html(html_text: str, base_url: str) -> dict[str, dict[str, Any]]:
    """Extract titles and visible card text from a hydrated Getmatch listing."""
    tree = HTMLParser(html.unescape(html_text or ""))
    cards: dict[str, dict[str, Any]] = {}
    for anchor in tree.css("a[href]"):
        href = anchor.attributes.get("href")
        if not isinstance(href, str):
            continue
        url = canonicalize_vacancy_url(urljoin(base_url, href))
        if url is None:
            continue
        identity = vacancy_id_from_url(url)
        if identity is None:
            continue
        title = _strip_text(anchor.text())
        card: Any = anchor
        while card is not None and "vacan" not in str(
            card.attributes.get("class") or ""
        ).casefold():
            card = card.parent
        card_text = _strip_text((card or anchor).text(separator=" "))
        current = cards.get(identity)
        if current is None:
            cards[identity] = {"id": identity, "url": url, "title": title, "text": card_text}
        else:
            current["title"] = current["title"] or title
            if len(card_text) > len(str(current.get("text") or "")):
                current["text"] = card_text
    return cards


def _status_from_exception(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return status
    status = getattr(exc, "status_code", None)
    return status if isinstance(status, int) else None


def _protected_status(status: int | None) -> bool:
    return status in {429, 503}


async def _get(client: Any, url: str) -> Any:
    return await fetch_with_retry(client, url, follow_redirects=True)


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
            "Getmatch listing pages are SPA shells with no free-text search; "
            "the dedicated parser discovers via public /api/offers with offset "
            "pagination and falls back to the sitemap, then extracts server-rendered "
            "detail HTML. Target roles ride on ?query= / _search_keywords and "
            "are matched against listing titles."
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
            extra={
                "api_page_size": 50,
                "max_listing_pages": 50,
                "detail_concurrency": 8,
                "browser_scroll_loops": 12,
                "browser_scroll_pause_ms": 500,
                "browser_scroll_px": 2500,
                "browser_stale_rounds": 3,
            },
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
        listing = urlunparse(parsed._replace(query=""))
        # Live search box does not exist and strips `?query=`. Keep the terms
        # on the URL so diagnostic ingest (no `_search_keywords`) still
        # title-filters /api/offers locally.
        return [with_query_params(listing, {"query": " OR ".join(terms)})]

    def _limit(self, spec_limit: int | None) -> int:
        if spec_limit is not None:
            return max(1, int(spec_limit))
        manifest_entry = getattr(self, "_manifest_entry", None)
        raw_limit = getattr(manifest_entry, "limit", None) if manifest_entry is not None else None
        if isinstance(raw_limit, (int, float, str)):
            return max(1, int(raw_limit))
        return 50

    def _extra_value(self, spec: CareerSiteSpec, key: str, default: object) -> object:
        value = spec.monitor_config.get(key)
        if value is not None:
            return value
        manifest_entry = getattr(self, "_manifest_entry", None)
        extra = getattr(manifest_entry, "extra", {}) if manifest_entry is not None else {}
        if isinstance(extra, dict) and extra.get(key) is not None:
            return extra[key]
        return default

    def _api_page_size(self, spec: CareerSiteSpec, limit: int) -> int:
        configured = _as_int(self._extra_value(spec, "api_page_size", 50), 50)
        # Preserve small explicit page sizes for deterministic API contracts;
        # larger runs use the board's 50-row maximum.
        return max(1, min(50, max(limit, 1), configured))

    def _max_listing_pages(self, spec: CareerSiteSpec, limit: int, page_size: int) -> int:
        configured = _as_int(self._extra_value(spec, "max_listing_pages", 50), 50)
        required = (limit + page_size - 1) // page_size
        return max(1, min(200, max(configured, required)))

    def _detail_concurrency(self, spec: CareerSiteSpec) -> int:
        configured = _as_int(self._extra_value(spec, "detail_concurrency", 8), 8)
        return max(1, min(50, configured))

    def _browser_config(self, spec: CareerSiteSpec, bypass_strategy: Any) -> dict[str, Any]:
        config = resolve_browser_config(
            spec,
            bypass_strategy,
            {"headless": True, "stealth": False, "wait": "domcontentloaded"},
        )
        config["_bypass_strategy"] = bypass_strategy
        return config

    async def _browser_search_box(
        self,
        page: Any,
        keywords: Sequence[str],
        *,
        timeout_ms: int,
    ) -> bool:
        """Use a search box if Getmatch adds one; current UI has filters only."""
        if not keywords:
            return False
        for selector in (
            'input[name="query"]',
            'input[type="search"]',
            'input[placeholder*="Поиск"]',
            'input[placeholder*="поиск"]',
        ):
            try:
                locator = page.locator(selector).first
                if await locator.count() == 0:
                    continue
                await locator.fill(" ".join(keywords))
                await locator.press("Enter")
                wait_for_load_state = getattr(page, "wait_for_load_state", None)
                if callable(wait_for_load_state):
                    await wait_for_load_state("domcontentloaded", timeout=timeout_ms)
                return True
            except Exception as exc:  # noqa: BLE001 - try the next site control
                logger.debug("getmatch.browser_search_box_failed", selector=selector, error=str(exc))
        return False

    async def _discover_with_browser(
        self,
        spec: CareerSiteSpec,
        *,
        limit: int,
        keywords: Sequence[str],
        bypass_strategy: Any,
        cards: dict[str, dict[str, Any]],
    ) -> list[str]:
        config = self._browser_config(spec, bypass_strategy)
        scroll_loops = _as_int(self._extra_value(spec, "browser_scroll_loops", 12), 12)
        pause_sec = _as_int(
            self._extra_value(spec, "browser_scroll_pause_ms", 500), 500
        ) / 1000
        scroll_px = _as_int(self._extra_value(spec, "browser_scroll_px", 2500), 2500)
        stale_rounds = _as_int(self._extra_value(spec, "browser_stale_rounds", 3), 3)
        collected: list[str] = []
        seen: set[str] = set()
        async with open_page(
            config,
            use_proxy=bool(getattr(bypass_strategy, "uses_proxy", False)),
            bypass_strategy=bypass_strategy,
        ) as page:
            await navigate(page, spec.url, config)
            page_url = str(getattr(page, "url", spec.url) or spec.url)
            content = await page.content()
            if is_challenge_response(content):
                raise BrowserChallengeError(url=page_url, challenge_type="getmatch_challenge")
            page_cards = _listing_cards_from_html(content, page_url)
            if not page_cards and await self._browser_search_box(
                page,
                keywords,
                timeout_ms=_as_int(config.get("timeout"), 30_000),
            ):
                content = await page.content()
                page_url = str(getattr(page, "url", page_url) or page_url)
                page_cards = _listing_cards_from_html(content, page_url)
            cards.update(page_cards)
            urls = await browser_scroll_collect_urls(
                page,
                page_url,
                _DETAIL_URL_RE,
                limit=limit,
                scroll_loops=scroll_loops,
                pause_sec=pause_sec,
                scroll_px=scroll_px,
                stale_rounds=stale_rounds,
            )
            content = await page.content()
            cards.update(_listing_cards_from_html(content, page_url))
            for raw_url in urls:
                canonical = canonicalize_vacancy_url(raw_url)
                if canonical is None:
                    continue
                identity = vacancy_id_from_url(canonical)
                if identity is None or identity in seen:
                    continue
                card = cards.get(identity)
                if keywords and not listing_matches_keywords(
                    str((card or {}).get("title") or ""),
                    str((card or {}).get("text") or canonical),
                    keywords,
                ):
                    continue
                seen.add(identity)
                collected.append(canonical)
                if len(collected) >= limit:
                    break
        return collected

    async def _browser_detail(
        self,
        spec: CareerSiteSpec,
        detail_url: str,
        bypass_strategy: Any,
    ) -> RawItem | None:
        config = self._browser_config(spec, bypass_strategy)
        async with open_page(
            config,
            use_proxy=bool(getattr(bypass_strategy, "uses_proxy", False)),
            bypass_strategy=bypass_strategy,
        ) as page:
            await navigate(page, detail_url, config)
            final_url = canonicalize_vacancy_url(
                str(getattr(page, "url", detail_url) or detail_url)
            )
            if final_url is None:
                return None
            content = await page.content()
            if is_challenge_response(content):
                raise BrowserChallengeError(url=final_url, challenge_type="getmatch_challenge")
            return item_from_detail_html(final_url, content, spec.source_name or "getmatch", spec.url)

    def _sitemap_url(self, board_url: str) -> str:
        parsed = urlparse(board_url)
        host = (parsed.hostname or "getmatch.ru").lower()
        if host.startswith("www."):
            host = host[4:]
        scheme = parsed.scheme or "https"
        return f"{scheme}://{host}/sitemap.xml"

    async def _discover_via_offers_api(
        self,
        spec: CareerSiteSpec,
        client: Any,
        keywords: Sequence[str] | None = None,
        *,
        cards: dict[str, dict[str, Any]] | None = None,
    ) -> list[str]:
        query = dict(parse_qsl(urlparse(spec.url or "").query, keep_blank_values=True))
        sphere = str(query.get("sp") or "").strip()
        limit = self._limit(spec.limit)
        seen: set[str] = set()
        urls: list[str] = []
        offset = 0
        page_size = self._api_page_size(spec, limit)
        max_pages = self._max_listing_pages(spec, limit, page_size)
        for _ in range(max_pages):
            params: dict[str, str] = {}
            if sphere:
                params["sp"] = sphere
            params.update(
                {
                    "sa": "any",
                    "pa": "all",
                    "offset": str(offset),
                    "limit": str(page_size),
                }
            )
            api_url = with_query_params(_OFFERS_API_URL, params)
            try:
                response = await _get(client, api_url)
            except Exception as exc:
                if _status_from_exception(exc) == 429:
                    raise
                logger.debug("getmatch.offers_api_failed", url=api_url, error=str(exc))
                break
            status_code = getattr(response, "status_code", None)
            if _protected_status(status_code):
                response.raise_for_status()
            text = _response_text(response)
            if any(marker in text.casefold() for marker in _AUTH_MARKERS):
                logger.debug("getmatch.offers_api_auth_wall", url=api_url)
                break
            inventory = extract_vacancy_urls_from_offers(text, limit=page_size)
            total = _offers_total(text)
            page_urls = extract_vacancy_urls_from_offers(
                text, limit=max(limit, page_size), seen=seen, keywords=keywords or ()
            )
            if not inventory:
                break
            for card in _offer_cards_from_payload(text, keywords=keywords or ()):
                if cards is not None:
                    cards[card["id"]] = card
            for url in page_urls:
                urls.append(url)
                if len(urls) >= limit:
                    break
            if total is not None and offset + page_size >= total:
                break
            if total is None and len(inventory) < page_size:
                break
            offset += page_size
        return urls[:limit]

    async def discover(
        self,
        spec: CareerSiteSpec,
        client: Any,
        *,
        cards: dict[str, dict[str, Any]] | None = None,
    ) -> list[str]:
        """Return canonical detail URLs (API, listing HTML, then sitemap)."""
        limit = self._limit(spec.limit)
        keywords = _keywords_from_spec(spec)
        board_url = spec.url or _DEFAULT_BOARD_URL
        card_store = cards if cards is not None else {}
        bypass_strategy = spec.monitor_config.get("_bypass_strategy")
        seen: set[str] = set()
        urls: list[str] = []

        # Detail URL shortcut: operator added a single vacancy.
        direct = canonicalize_vacancy_url(board_url)
        if direct is not None:
            return [direct]

        api_urls = await self._discover_via_offers_api(
            spec, client, keywords, cards=card_store
        )
        if api_urls:
            return api_urls[:limit]

        try:
            listing_response = await _get(client, board_url)
        except Exception as exc:
            logger.debug("getmatch.listing_fetch_failed", url=board_url, error=str(exc))
            listing_response = None

        if listing_response is not None:
            if _protected_status(getattr(listing_response, "status_code", None)):
                listing_response.raise_for_status()
            listing_kind, listing_html = _classify_response(
                listing_response,
                url=board_url,
                expected="listing",
            )
            if listing_kind is GetmatchPageKind.CHALLENGE:
                if bypass_strategy is not None:
                    return await self._discover_with_browser(
                        spec,
                        limit=limit,
                        keywords=keywords,
                        bypass_strategy=bypass_strategy,
                        cards=card_store,
                    )
                _raise_for_kind(
                    listing_kind,
                    url=board_url,
                    message="listing page is an anti-bot challenge wall",
                )
            if listing_kind is GetmatchPageKind.AUTH_WALL:
                if bypass_strategy is not None:
                    return await self._discover_with_browser(
                        spec,
                        limit=limit,
                        keywords=keywords,
                        bypass_strategy=bypass_strategy,
                        cards=card_store,
                    )
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
                card_store.update(
                    _listing_cards_from_html(
                        listing_html,
                        str(getattr(listing_response, "url", board_url) or board_url),
                    )
                )
                listing_urls = extract_vacancy_urls_from_html(
                    listing_html,
                    str(getattr(listing_response, "url", board_url) or board_url),
                    limit=limit,
                    seen=seen,
                )
                if keywords:
                    listing_urls = [
                        url
                        for url in listing_urls
                        if listing_matches_keywords(
                            str(
                                card_store.get(vacancy_id_from_url(url) or "", {}).get(
                                    "title", ""
                                )
                            ),
                            str(
                                card_store.get(vacancy_id_from_url(url) or "", {}).get(
                                    "text", url
                                )
                            ),
                            keywords,
                        )
                    ]
                urls.extend(listing_urls)
                if urls:
                    return urls[:limit]

        sitemap_url = self._sitemap_url(board_url)
        try:
            sitemap_response = await _get(client, sitemap_url)
        except Exception as exc:
            logger.debug("getmatch.sitemap_fetch_failed", url=sitemap_url, error=str(exc))
            if _status_from_exception(exc) == 429:
                raise
            if bypass_strategy is not None:
                return await self._discover_with_browser(
                    spec,
                    limit=limit,
                    keywords=keywords,
                    bypass_strategy=bypass_strategy,
                    cards=card_store,
                )
            if not urls:
                raise GetmatchIngestError(
                    "parser_error",
                    f"failed to fetch sitemap: {exc}",
                    url=sitemap_url,
                ) from exc
            return urls[:limit]

        if _protected_status(getattr(sitemap_response, "status_code", None)):
            sitemap_response.raise_for_status()

        sitemap_kind, sitemap_text = _classify_response(
            sitemap_response,
            url=sitemap_url,
            expected="sitemap",
        )
        if sitemap_kind is GetmatchPageKind.CHALLENGE:
            if bypass_strategy is not None:
                return await self._discover_with_browser(
                    spec,
                    limit=limit,
                    keywords=keywords,
                    bypass_strategy=bypass_strategy,
                    cards=card_store,
                )
            _raise_for_kind(
                sitemap_kind,
                url=sitemap_url,
                message="sitemap response is an anti-bot challenge wall",
            )
        if sitemap_kind is GetmatchPageKind.AUTH_WALL:
            if bypass_strategy is not None:
                return await self._discover_with_browser(
                    spec,
                    limit=limit,
                    keywords=keywords,
                    bypass_strategy=bypass_strategy,
                    cards=card_store,
                )
            _raise_for_kind(
                sitemap_kind,
                url=sitemap_url,
                message="sitemap requires authentication",
            )
        if sitemap_kind is GetmatchPageKind.LAYOUT_CHANGED:
            if bypass_strategy is not None:
                return await self._discover_with_browser(
                    spec,
                    limit=limit,
                    keywords=keywords,
                    bypass_strategy=bypass_strategy,
                    cards=card_store,
                )
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
        if not ordered and bypass_strategy is not None:
            return await self._discover_with_browser(
                spec,
                limit=limit,
                keywords=keywords,
                bypass_strategy=bypass_strategy,
                cards=card_store,
            )
        return ordered

    async def parse(
        self,
        spec: CareerSiteSpec,
        client: Any,
    ) -> AsyncIterator[RawItem]:
        source_name = spec.source_name or "getmatch"
        board_url = spec.url or _DEFAULT_BOARD_URL
        bypass_strategy = spec.monitor_config.get("_bypass_strategy")
        cards: dict[str, dict[str, Any]] = {}
        detail_urls = await self.discover(spec, client, cards=cards)
        if not detail_urls:
            # Explicit empty is not a failure; CareerSiteSource maps this via
            # confirmed_empty_on_empty when parse yields nothing.
            return

        stats = spec.monitor_config.get("_pipeline_stats")
        if stats is not None:
            stats.parser_urls_discovered = len(detail_urls)

        detail_limit = spec.detail_limit
        detail_urls_to_fetch = (
            detail_urls
            if detail_limit is None
            else detail_urls[: max(0, int(detail_limit))]
        )
        listing_only_urls = detail_urls[len(detail_urls_to_fetch) :]
        emitted = 0
        detail_errors: list[Exception] = []

        async def load_detail(
            detail_url: str,
        ) -> tuple[str, RawItem | None, Exception | None]:
            try:
                response = await _get(client, detail_url)
                status_code = getattr(response, "status_code", None)
                if _protected_status(status_code):
                    response.raise_for_status()
                if isinstance(status_code, int) and status_code >= 400:
                    kind = classify_getmatch_payload(
                        _response_text(response),
                        content_type=_response_content_type(response),
                        status_code=status_code,
                        expected="detail",
                    )
                    if kind in {GetmatchPageKind.AUTH_WALL, GetmatchPageKind.CHALLENGE}:
                        if bypass_strategy is not None:
                            return detail_url, await self._browser_detail(
                                spec, detail_url, bypass_strategy
                            ), None
                        _raise_for_kind(
                            kind,
                            url=detail_url,
                            message=f"detail fetch returned {status_code}",
                        )
                    return detail_url, None, None
                html_text = _response_text(response)
                final_url = str(getattr(response, "url", detail_url) or detail_url)
                kind = classify_getmatch_payload(html_text, expected="detail")
                if kind is GetmatchPageKind.CHALLENGE:
                    if bypass_strategy is not None:
                        return detail_url, await self._browser_detail(
                            spec, detail_url, bypass_strategy
                        ), None
                    _raise_for_kind(
                        kind,
                        url=final_url,
                        message="detail page is an anti-bot challenge wall",
                    )
                item = item_from_detail_html(final_url, html_text, source_name, board_url)
                if item is None and bypass_strategy is not None:
                    item = await self._browser_detail(spec, detail_url, bypass_strategy)
                return detail_url, item, None
            except (BrowserChallengeError, GetmatchIngestError) as exc:
                return detail_url, None, exc
            except Exception as exc:
                if _status_from_exception(exc) in {429, 503}:
                    raise
                return detail_url, None, exc

        tasks = [asyncio.create_task(load_detail(url)) for url in detail_urls_to_fetch]
        try:
            for task in asyncio.as_completed(tasks):
                detail_url, item, error = await task
                if error is not None:
                    if isinstance(error, BrowserChallengeError):
                        raise error
                    if isinstance(error, GetmatchIngestError) and error.kind in {
                        "challenge_required",
                        "auth_wall",
                    }:
                        if error.kind == "challenge_required":
                            raise BrowserChallengeError(
                                url=error.url or board_url,
                                challenge_type="getmatch_challenge",
                            ) from error
                        raise error
                    detail_errors.append(error)
                    continue
                if item is None:
                    item = item_from_offer_card(
                        cards.get(vacancy_id_from_url(detail_url) or "", {}),
                        source_name,
                        board_url,
                    )
                if item is None:
                    continue
                if item.metadata.get("archived") is True:
                    continue
                yield item
                emitted += 1
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        # Cards are a lossless fallback for API results whose detail endpoint
        # is temporarily unavailable or has a harmless layout drift.
        for detail_url in listing_only_urls:
            card_item = item_from_offer_card(
                cards.get(vacancy_id_from_url(detail_url) or "", {}),
                source_name,
                board_url,
            )
            if card_item is not None:
                yield card_item
                emitted += 1

        if emitted == 0 and detail_urls:
            if detail_errors:
                raise detail_errors[0]
            # Had candidates but none produced usable drafts.
            raise GetmatchIngestError(
                "layout_changed",
                "detail pages did not yield usable vacancy drafts",
                url=board_url,
            )

    @property
    def __name__(self) -> str:
        return "GetmatchParser"
