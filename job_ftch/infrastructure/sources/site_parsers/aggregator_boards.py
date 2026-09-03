"""Special parsers for vacancy aggregators with optional origin links."""

from __future__ import annotations

import base64
import binascii
import html
import json
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse, urlunparse

import structlog
from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.network.ssrf_guard import check_ssrf
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    DEFAULT_LISTING_MAX_PAGES,
    ListingPagination,
    keywords_from_spec,
    normalize_search_keywords,
    paginate_listing,
    safe_fetch,
    text_matches_keywords,
    with_query_params,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

logger = structlog.get_logger(__name__)

_EXTERNAL_CTA_RE = re.compile(
    r"\b(?:apply|отклик|отправить резюме|перейти к вакансии|view (?:job|opening)|open job)\b",
    re.IGNORECASE,
)
_JOB_PATH_RE = re.compile(r"/(?:job|jobs|vacanc(?:y|ies)|career|careers|position|opening)s?/", re.I)
_IGNORED_EXTERNAL_HOSTS = frozenset(
    {
        "facebook.com",
        "github.com",
        "instagram.com",
        "linkedin.com",
        "t.me",
        "twitter.com",
        "x.com",
        "youtube.com",
    }
)


def _clean_text(value: str) -> str:
    return " ".join(value.split())


def _page_text(html: str) -> tuple[str, str]:
    tree = LexborHTMLParser(html or "")
    title_node = tree.css_first("h1") or tree.css_first("title")
    title = _clean_text(title_node.text(separator=" ", strip=True)) if title_node else ""
    body = ""
    for selector in ("main", "article", '[class*="description"]', '[class*="job"]', "body"):
        node = tree.css_first(selector)
        if node is None:
            continue
        body = _clean_text(node.text(separator=" ", strip=True))
        if body:
            break
    if title and body.casefold().startswith(title.casefold()):
        body = body[len(title) :].strip()
    return title, body


def _same_host(left: str, right: str) -> bool:
    left_host = (urlparse(left).hostname or "").casefold().removeprefix("www.")
    right_host = (urlparse(right).hostname or "").casefold().removeprefix("www.")
    return bool(left_host and left_host == right_host)


def _canonical_http_url(raw_url: Any, base_url: str) -> str | None:
    if not isinstance(raw_url, str) or not raw_url.strip():
        return None
    absolute = urljoin(base_url, raw_url.strip())
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return urlunparse(parsed._replace(fragment=""))


def _external_candidates(html: str, base_url: str) -> Iterable[str]:
    tree = LexborHTMLParser(html or "")
    seen: set[str] = set()
    for anchor in tree.css("a[href]"):
        href = _canonical_http_url(anchor.attributes.get("href"), base_url)
        if href is None or _same_host(href, base_url) or href in seen:
            continue
        host = (urlparse(href).hostname or "").casefold().removeprefix("www.")
        if host in _IGNORED_EXTERNAL_HOSTS:
            continue
        signal = " ".join(
            str(anchor.attributes.get(key) or "") for key in ("aria-label", "class", "id", "rel")
        )
        signal = f"{signal} {_clean_text(anchor.text(separator=' ', strip=True))} {href}"
        score = int(bool(_EXTERNAL_CTA_RE.search(signal))) + int(bool(_JOB_PATH_RE.search(href)))
        if score < 1:
            continue
        seen.add(href)
        yield href


def _json_urls(value: Any, base_url: str) -> Iterable[str]:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in {"url", "jobUrl", "job_url", "applyUrl", "apply_url"}:
                candidate = _canonical_http_url(nested, base_url)
                if candidate and not _same_host(candidate, base_url):
                    yield candidate
            yield from _json_urls(nested, base_url)
    elif isinstance(value, list):
        for nested in value:
            yield from _json_urls(nested, base_url)


def _external_url_from_html(html: str, base_url: str) -> str | None:
    for candidate in _external_candidates(html, base_url):
        return candidate
    tree = LexborHTMLParser(html or "")
    for node in tree.css('script[type="application/ld+json"]'):
        try:
            payload = json.loads(node.text())
        except (TypeError, json.JSONDecodeError):
            continue
        for candidate in _json_urls(payload, base_url):
            return candidate
    return None


def _detail_urls(html: str, base_url: str, pattern: re.Pattern[str], limit: int) -> list[str]:
    tree = LexborHTMLParser(html or "")
    urls: list[str] = []
    seen: set[str] = set()
    for anchor in tree.css("a[href]"):
        url = _canonical_http_url(anchor.attributes.get("href"), base_url)
        if url is None or not _same_host(url, base_url) or not pattern.search(urlparse(url).path):
            continue
        if url in seen or url.rstrip("/") == base_url.rstrip("/"):
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= limit:
            break
    return urls


def _matches_target_roles(spec: CareerSiteSpec, text: str) -> bool:
    return text_matches_keywords(text, keywords_from_spec(spec))


def _agile_token_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value.startswith("eyJ"):
        return _canonical_http_url(value, "https://jobboard.agilefluent.ru/")
    parts = value.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        decoded = json.loads(payload)
    except (ValueError, TypeError, binascii.Error, json.JSONDecodeError):
        return None
    return _canonical_http_url(
        decoded.get("url") if isinstance(decoded, dict) else None,
        "https://jobboard.agilefluent.ru/",
    )


class HtmlAggregatorParser:
    """Follow aggregator detail pages and, when exposed, origin vacancy pages."""

    domain_pattern: str
    has_custom_parse = True
    supports_discover = False
    supports_search = False
    search_mode = "none"
    follow_origin = True
    confirmed_empty_on_empty = False
    detail_pattern = re.compile(r"$^", re.IGNORECASE)
    parser_name = "aggregator"

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=False, wait="domcontentloaded", include_if_detail_page=True
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    def build_search_urls(
        self, base_url: str, keywords: Any, *, limit: int | None = None
    ) -> list[str]:
        del base_url, keywords, limit
        return []

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        limit = spec.limit or 50
        try:
            response = await safe_fetch(client, spec.url)
        except Exception as exc:  # noqa: BLE001 - source fallback handles blocked boards
            logger.info("aggregator_listing_fetch_failed", parser=self.parser_name, error=str(exc))
            return
        current_url = str(response.url)
        roles = normalize_search_keywords(spec.monitor_config.get("_search_keywords"))
        discovery_limit = min(max(limit, 1) * 3, 150) if roles else limit
        if self.detail_pattern.search(urlparse(current_url).path):
            urls = [current_url]
        else:
            urls = _detail_urls(
                str(response.text), current_url, self.detail_pattern, discovery_limit
            )
        emitted = 0
        for aggregator_url in urls:
            item = await self._parse_detail(spec, client, aggregator_url)
            if item is None or not _matches_target_roles(spec, item.text):
                continue
            yield item
            emitted += 1
            if emitted >= limit:
                return

    async def _parse_detail(
        self, spec: CareerSiteSpec, client: Any, aggregator_url: str
    ) -> RawItem | None:
        try:
            response = await safe_fetch(client, aggregator_url)
            html = str(response.text)
            final_aggregator_url = str(response.url)
        except Exception as exc:  # noqa: BLE001 - keep listing fallback below
            logger.debug("aggregator_detail_fetch_failed", url=aggregator_url, error=str(exc))
            return None
        title, body = _page_text(html)
        if not title and not body:
            return None
        origin_url = _external_url_from_html(html, final_aggregator_url)
        origin_final_url = None
        origin_body = ""
        if self.follow_origin and origin_url:
            try:
                await check_ssrf(origin_url)
                origin_response = await safe_fetch(client, origin_url)
                origin_final_url = str(origin_response.url)
                if not _same_host(origin_final_url, final_aggregator_url):
                    origin_title, origin_body = _page_text(str(origin_response.text))
                    title = origin_title or title
            except Exception as exc:  # noqa: BLE001 - aggregator detail remains usable
                logger.debug("aggregator_origin_fetch_failed", url=origin_url, error=str(exc))
        text = "\n".join(part for part in (title, body, origin_body) if part)
        canonical_url = origin_final_url or final_aggregator_url
        external_id = urlparse(final_aggregator_url).path.rstrip("/").rsplit("/", 1)[-1]
        return build_raw_item(
            source_kind=SourceKind.CAREER_SITE,
            source_name=spec.source_name or self.parser_name,
            external_id=external_id or canonical_url,
            url=canonical_url,
            text=text,
            metadata={
                "adapter": self.parser_name,
                "parser": self.parser_name,
                "board_url": spec.url,
                "aggregator_url": final_aggregator_url,
                "origin_url": origin_url,
                "detail_vacancy_confirmed": True,
                "origin_fetched": bool(origin_final_url),
            },
        )


class AgileFluentParser(HtmlAggregatorParser):
    domain_pattern = r"^https?://jobboard\.agilefluent\.ru(?:/|$)"
    parser_name = "agilefluent"
    supports_search = True
    search_mode = "combined"
    follow_origin = False
    confirmed_empty_on_empty = True
    _API_URL = "https://jobboard.agilefluent.ru/api/jobs/search"

    def build_search_urls(
        self, base_url: str, keywords: Any, *, limit: int | None = None
    ) -> list[str]:
        del limit
        if not normalize_search_keywords(keywords):
            return []
        parsed = urlparse(base_url)
        listing = urlunparse(parsed._replace(query="", fragment=""))
        return [listing or "https://jobboard.agilefluent.ru/"]

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        limit = spec.limit or 50
        keywords = keywords_from_spec(spec)
        page_size = min(50, max(limit, 1))
        emitted = 0
        seen: set[str] = set()
        for page in range(1, DEFAULT_LISTING_MAX_PAGES + 1):
            try:
                response = await client.post(
                    self._API_URL,
                    json={"filters": {}, "pagination": {"limit": page_size, "page": page}},
                    follow_redirects=True,
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:  # noqa: BLE001 - normal source fallback
                logger.info("agilefluent_api_search_failed", error=str(exc))
                return
            rows = payload.get("data", []) if isinstance(payload, dict) else []
            if not isinstance(rows, list) or not rows:
                return
            for row in rows:
                if not isinstance(row, dict):
                    continue
                job_id = row.get("id")
                title = _clean_text(str(row.get("title") or ""))
                if job_id is None or not title:
                    continue
                external_id = str(job_id)
                if external_id in seen:
                    continue
                seen.add(external_id)
                body = _clean_text(str(row.get("description") or ""))
                company = _clean_text(str(row.get("companyName") or ""))
                text = "\n".join(part for part in (title, company, body) if part)
                if not text_matches_keywords(text, keywords):
                    continue
                aggregator_url = f"https://jobboard.agilefluent.ru/api/jobs/{job_id}"
                yield build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=spec.source_name or self.parser_name,
                    external_id=external_id,
                    url=aggregator_url,
                    text=text,
                    metadata={
                        "adapter": self.parser_name,
                        "parser": self.parser_name,
                        "board_url": spec.url,
                        "aggregator_url": aggregator_url,
                        "origin_url": _agile_token_url(row.get("url")),
                        "detail_vacancy_confirmed": True,
                        "origin_fetched": False,
                    },
                )
                emitted += 1
                if emitted >= limit:
                    return
            has_more = bool(payload.get("hasMore")) if isinstance(payload, dict) else False
            if not has_more:
                return


class QuickOfferParser(HtmlAggregatorParser):
    domain_pattern = r"^https?://quick-offer\.ru(?:/|$)"
    parser_name = "quick_offer"
    detail_pattern = re.compile(r"/job/[^/?#]+/?$", re.IGNORECASE)


class DjinniAggregatorParser(HtmlAggregatorParser):
    domain_pattern = r"^https?://djinni\.co(?:/|$)"
    parser_name = "djinni_aggregator"
    detail_pattern = re.compile(r"/jobs/\d+-[a-z0-9-]+/?$", re.IGNORECASE)
    supports_search = True
    search_mode = "per_keyword"

    def build_search_urls(
        self, base_url: str, keywords: Any, *, limit: int | None = None
    ) -> list[str]:
        del limit
        return [
            with_query_params(
                "https://djinni.co/jobs/",
                {"all_keywords": term, "search_type": "title-only"},
            )
            for term in normalize_search_keywords(keywords)
        ]


_FOORILLA_JOBS = "https://foorilla.com/hiring/jobs/"
_FOORILLA_HTMX_HEADERS = {
    "HX-Request": "true",
    "HX-Target": "job-list",
    "HX-Current-URL": "https://foorilla.com/hiring/",
    "Referer": "https://foorilla.com/hiring/",
    "Accept": "text/html",
}
_FOORILLA_JOB_RE = re.compile(r"/hiring/jobs/([a-z0-9-]+)-(\d+)/?", re.IGNORECASE)
_AIJOBS_COM_JOB_RE = re.compile(r"/jobs/(\d+)-([^/?#]+)/?", re.IGNORECASE)
_AIJOBS_AI_JOB_RE = re.compile(r"/job/([^/?#]+)/?", re.IGNORECASE)


class AIJobsParser(HtmlAggregatorParser):
    """aijobs.net redirects to foorilla.com; listing is an HTMX fragment."""

    domain_pattern = r"^https?://(?:(?:www\.)?aijobs\.net|(?:www\.)?foorilla\.com)(?:/|$)"
    parser_name = "aijobs"
    supports_search = True
    search_mode = "combined"
    follow_origin = False
    confirmed_empty_on_empty = True
    detail_pattern = _FOORILLA_JOB_RE

    def build_search_urls(
        self, base_url: str, keywords: Any, *, limit: int | None = None
    ) -> list[str]:
        del base_url, limit
        if not normalize_search_keywords(keywords):
            return []
        return [_FOORILLA_JOBS]

    def _items_from_html(
        self, html_text: str, board_url: str, source_name: str, keywords: list[str]
    ) -> list[RawItem]:
        del keywords
        tree = LexborHTMLParser(html_text or "")
        items: list[RawItem] = []
        seen: set[str] = set()
        for node in tree.css("[hx-get]"):
            href = html.unescape(str(node.attributes.get("hx-get") or ""))
            match = _FOORILLA_JOB_RE.search(href)
            if match is None:
                continue
            url = urljoin("https://foorilla.com", match.group(0))
            if url in seen:
                continue
            seen.add(url)
            card = node
            for _ in range(4):
                parent = getattr(card, "parent", None)
                if parent is None:
                    break
                card = parent
                if "list-group-item" in str(card.attributes.get("class") or ""):
                    break
            text = _clean_text(html.unescape(card.text(separator="\n", strip=True)))
            title = _clean_text(html.unescape(node.text(separator=" ", strip=True))) or text
            if not title:
                continue
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=source_name,
                    external_id=match.group(2),
                    url=url,
                    text="\n".join(part for part in (title, text) if part),
                    metadata={
                        "adapter": self.parser_name,
                        "parser": self.parser_name,
                        "board_url": board_url,
                        "detail_vacancy_confirmed": True,
                        "origin_fetched": False,
                    },
                )
            )
        return items

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        keywords = keywords_from_spec(spec)
        limit = spec.limit or 50
        source_name = spec.source_name or self.parser_name
        terms = keywords or [""]
        seen: set[str] = set()
        emitted = 0

        async def fetch(url: str) -> str:
            response = await client.get(
                url, follow_redirects=True, headers=_FOORILLA_HTMX_HEADERS
            )
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            return str(response.text)

        def extract(html_text: str, url: str) -> list[RawItem]:
            return self._items_from_html(html_text, url, source_name, keywords)

        for term in terms:
            start = (
                with_query_params(_FOORILLA_JOBS, {"job_search": term}) if term else _FOORILLA_JOBS
            )
            page_items = await paginate_listing(
                fetch,
                extract,
                start,
                limit=limit,
                pagination=ListingPagination(max_pages=DEFAULT_LISTING_MAX_PAGES),
                identity=lambda item: item.url,
            )
            for item in page_items:
                key = str(item.url)
                if key in seen:
                    continue
                seen.add(key)
                yield item
                emitted += 1
                if emitted >= limit:
                    return


class AIJobsComParser(HtmlAggregatorParser):
    domain_pattern = r"^https?://(?:www\.)?aijobs\.com(?:/|$)"
    parser_name = "aijobs_com"
    supports_search = True
    search_mode = "combined"
    follow_origin = False
    confirmed_empty_on_empty = True
    detail_pattern = _AIJOBS_COM_JOB_RE
    _LISTING = "https://www.aijobs.com/jobs"

    def build_search_urls(
        self, base_url: str, keywords: Any, *, limit: int | None = None
    ) -> list[str]:
        del base_url, limit
        terms = normalize_search_keywords(keywords)
        if not terms:
            return []
        return [with_query_params(self._LISTING, {"q": " OR ".join(terms)})]

    def _items_from_html(
        self, html_text: str, board_url: str, source_name: str, keywords: list[str]
    ) -> list[RawItem]:
        del keywords
        tree = LexborHTMLParser(html_text or "")
        items: list[RawItem] = []
        seen: set[str] = set()
        for anchor in tree.css("a[href]"):
            href = str(anchor.attributes.get("href") or "")
            match = _AIJOBS_COM_JOB_RE.search(href)
            if match is None or "/apply" in href:
                continue
            url = urljoin(board_url, href.split("?", 1)[0])
            if url in seen:
                continue
            seen.add(url)
            title = _clean_text(anchor.text(separator=" ", strip=True))
            if not title:
                title = match.group(2).replace("-", " ")
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=source_name,
                    external_id=match.group(1),
                    url=url,
                    text=title,
                    metadata={
                        "adapter": self.parser_name,
                        "parser": self.parser_name,
                        "board_url": board_url,
                        "detail_vacancy_confirmed": True,
                        "origin_fetched": False,
                    },
                )
            )
        return items

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        keywords = keywords_from_spec(spec)
        limit = spec.limit or 50
        source_name = spec.source_name or self.parser_name
        start = spec.url if urlparse(spec.url).path.rstrip("/") else self._LISTING
        if keywords and "q=" not in start:
            start = with_query_params(self._LISTING, {"q": " OR ".join(keywords)})

        async def fetch(url: str) -> str:
            response = await safe_fetch(client, url)
            return str(response.text)

        def extract(html_text: str, url: str) -> list[RawItem]:
            return self._items_from_html(html_text, url, source_name, keywords)

        items = await paginate_listing(
            fetch,
            extract,
            start,
            limit=limit,
            pagination=ListingPagination(max_pages=DEFAULT_LISTING_MAX_PAGES),
            identity=lambda item: item.url,
        )
        if not items and keywords:
            items = await paginate_listing(
                fetch,
                extract,
                self._LISTING,
                limit=limit,
                pagination=ListingPagination(max_pages=DEFAULT_LISTING_MAX_PAGES),
                identity=lambda item: item.url,
            )
        for item in items:
            yield item


class AIJobsAiParser(HtmlAggregatorParser):
    domain_pattern = r"^https?://(?:www\.)?aijobs\.ai(?:/|$)"
    parser_name = "aijobs_ai"
    supports_search = True
    search_mode = "combined"
    follow_origin = False
    confirmed_empty_on_empty = True
    detail_pattern = _AIJOBS_AI_JOB_RE
    _LISTING = "https://aijobs.ai/jobs"

    def build_search_urls(
        self, base_url: str, keywords: Any, *, limit: int | None = None
    ) -> list[str]:
        del base_url, limit
        terms = normalize_search_keywords(keywords)
        if not terms:
            return []
        return [with_query_params(self._LISTING, {"keyword": " OR ".join(terms)})]

    def _items_from_html(
        self, html_text: str, board_url: str, source_name: str, keywords: list[str]
    ) -> list[RawItem]:
        del keywords
        tree = LexborHTMLParser(html_text or "")
        items: list[RawItem] = []
        seen: set[str] = set()
        for anchor in tree.css("a[href]"):
            href = str(anchor.attributes.get("href") or "")
            parsed = urlparse(urljoin(board_url, href))
            match = _AIJOBS_AI_JOB_RE.search(parsed.path)
            if match is None:
                continue
            url = urlunparse(parsed._replace(query="", fragment=""))
            if url in seen:
                continue
            seen.add(url)
            title = _clean_text(anchor.text(separator=" ", strip=True))
            if not title:
                title = match.group(1).replace("-", " ")
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=source_name,
                    external_id=match.group(1),
                    url=url,
                    text=title,
                    metadata={
                        "adapter": self.parser_name,
                        "parser": self.parser_name,
                        "board_url": board_url,
                        "detail_vacancy_confirmed": True,
                        "origin_fetched": False,
                    },
                )
            )
        return items

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        keywords = keywords_from_spec(spec)
        limit = spec.limit or 50
        source_name = spec.source_name or self.parser_name
        start = spec.url if "/job" in urlparse(spec.url).path else self._LISTING
        if keywords and "keyword=" not in start:
            start = with_query_params(self._LISTING, {"keyword": " OR ".join(keywords)})

        async def fetch(url: str) -> str:
            response = await safe_fetch(client, url)
            return str(response.text)

        def extract(html_text: str, url: str) -> list[RawItem]:
            return self._items_from_html(html_text, url, source_name, keywords)

        items = await paginate_listing(
            fetch,
            extract,
            start,
            limit=limit,
            pagination=ListingPagination(max_pages=DEFAULT_LISTING_MAX_PAGES),
            identity=lambda item: item.url,
        )
        if not items and keywords:
            items = await paginate_listing(
                fetch,
                extract,
                self._LISTING,
                limit=limit,
                pagination=ListingPagination(max_pages=DEFAULT_LISTING_MAX_PAGES),
                identity=lambda item: item.url,
            )
        for item in items:
            yield item


class AIEngineerParser(HtmlAggregatorParser):
    domain_pattern = r"^https?://ai\.engineer(?:/|$)"
    parser_name = "ai_engineer"
    detail_pattern = re.compile(r"$^", re.IGNORECASE)
    terminal_on_empty = True


class RemoteRocketshipParser(HtmlAggregatorParser):
    domain_pattern = r"^https?://(?:www\.)?remoterocketship\.com(?:/|$)"
    parser_name = "remote_rocketship"
    detail_pattern = re.compile(r"/jobs/[a-z0-9-]+/?$", re.IGNORECASE)

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=True,
            wait="domcontentloaded",
            include_if_detail_page=False,
        )


def _register(name: str, parser: type[HtmlAggregatorParser]) -> None:
    register_site_parser(
        name,
        domain_pattern=parser.domain_pattern,
        assessment_hint=known_board_assessment_hint(
            "known_site",
            f"site_parser:{parser.parser_name}",
            has_stable_url=True,
            can_detect_freshness_without_snapshot=False,
            rationale="Aggregator parser follows canonical detail pages and optional origin vacancy links.",
        ),
    )(parser)


_register("agilefluent", AgileFluentParser)
_register("quick_offer", QuickOfferParser)
_register("aijobs", AIJobsParser)
_register("aijobs_com", AIJobsComParser)
_register("aijobs_ai", AIJobsAiParser)
_register("ai_engineer", AIEngineerParser)
_register("remote_rocketship", RemoteRocketshipParser)


__all__ = [
    "AIEngineerParser",
    "AIJobsAiParser",
    "AIJobsComParser",
    "AIJobsParser",
    "AgileFluentParser",
    "DjinniAggregatorParser",
    "HtmlAggregatorParser",
    "QuickOfferParser",
    "RemoteRocketshipParser",
]
