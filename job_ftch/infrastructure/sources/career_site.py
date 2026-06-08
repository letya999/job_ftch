"""Career-site source adapters with parser auto-detection."""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urljoin, urlsplit

import httpx
from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import (
    register_parser,
    register_source,
    resolve_career_site_parser,
)
from job_ftch.domain import RawItem, SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from types import TracebackType

    from job_ftch.config import Settings


_GREENHOUSE_JOB_ID_RE = re.compile(r"/jobs/(?P<job_id>\d+)")
_BCC_JOB_ID_RE = re.compile(r"/career/(?P<job_id>\d+)/?$")


class _RetryingHttpClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        max_retries: int,
        retry_delay_seconds: float,
    ) -> None:
        self._client = client
        self._max_retries = max_retries
        self._retry_delay_seconds = retry_delay_seconds

    async def __aenter__(self) -> _RetryingHttpClient:
        await self._client.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._client.__aexit__(exc_type, exc, tb)

    async def get(self, url: str, *, follow_redirects: bool = False) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.get(url, follow_redirects=follow_redirects)
                response.raise_for_status()
                return response
            except Exception as exc:
                if attempt >= self._max_retries or not _is_retryable_http_error(exc):
                    raise
                await asyncio.sleep(self._retry_delay_seconds * (attempt + 1))
        msg = "career-site retry loop exhausted unexpectedly"
        raise RuntimeError(msg)


@asynccontextmanager
async def _http_session(client: Any, *, own_client: bool) -> AsyncIterator[Any]:
    if own_client:
        async with client as managed_client:
            yield managed_client
        return
    yield client


def _clean_text(value: str) -> str:
    return " ".join(value.split())


def _source_name_from_url(url: str) -> str:
    path = urlsplit(url).path.strip("/")
    return path.split("/")[-1] or "career-site"


def _is_retryable_http_error(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return False


class _CareerSiteParser(Protocol):
    async def parse(
        self,
        *,
        client: Any,
        url: str,
        html: str,
        limit: int,
    ) -> list[RawItem]:
        """Parse a career-site page into RawItems."""


class _GreenhouseParser:
    def _title_without_badge(self, node: Any) -> str:
        primary_text = node.text(separator=" ", strip=True)
        badge = node.css_first(".tag-container, .tag-text")
        if badge is None:
            return _clean_text(primary_text)
        badge_text = _clean_text(badge.text(separator=" ", strip=True))
        cleaned = primary_text.replace(badge_text, " ", 1) if badge_text else primary_text
        return _clean_text(cleaned)

    def _extract_source_name(self, parser: LexborHTMLParser, url: str) -> str:
        og_title = parser.css_first('meta[property="og:title"]')
        if og_title is not None:
            value = og_title.attributes.get("content")
            if value:
                return _clean_text(value)
        return _source_name_from_url(url)

    def _parse_job_anchor(
        self,
        *,
        board_url: str,
        source_name: str,
        anchor: Any,
        section: str | None,
        team: str | None,
    ) -> RawItem | None:
        href = anchor.attributes.get("href")
        if not href:
            return None
        title_node = anchor.css_first("p.body--medium") or anchor
        title = self._title_without_badge(title_node)
        location_node = anchor.css_first("p.body__secondary")
        location = (
            _clean_text(location_node.text(separator=" ", strip=True))
            if location_node is not None
            else None
        )
        full_url = urljoin(board_url, href)
        job_match = _GREENHOUSE_JOB_ID_RE.search(full_url)
        external_id = job_match.group("job_id") if job_match is not None else href
        text_parts = [title, location, section, team]
        return build_raw_item(
            source_kind=SourceKind.CAREER_SITE,
            source_name=source_name,
            external_id=str(external_id),
            url=full_url,
            text="\n".join(part for part in text_parts if part),
            metadata={
                "board_url": board_url,
                "job_url": full_url,
                "department": section,
                "team": team,
                "location": location,
                "parser": "greenhouse",
            },
        )

    async def parse(
        self,
        *,
        client: Any,
        url: str,
        html: str,
        limit: int,
    ) -> list[RawItem]:
        parser = LexborHTMLParser(html)
        source_name = self._extract_source_name(parser, url)
        container = parser.css_first(".job-posts") or parser
        items: list[RawItem] = []
        current_section: str | None = None
        current_team: str | None = None
        selectors = "h3.section-header, h4.section-header, h4.sub-section-header, tr.job-post"

        for node in container.css(selectors):
            tag = node.tag
            if tag == "h3":
                current_section = _clean_text(node.text(separator=" ", strip=True))
                current_team = None
                continue
            if tag == "h4":
                current_team = _clean_text(node.text(separator=" ", strip=True))
                continue
            anchor = node.css_first('a[href*="/jobs/"]')
            if anchor is None:
                continue
            item = self._parse_job_anchor(
                board_url=url,
                source_name=source_name,
                anchor=anchor,
                section=current_section,
                team=current_team,
            )
            if item is not None:
                items.append(item)
            if len(items) >= limit:
                return items

        if items:
            return items

        for anchor in container.css('a[href*="/jobs/"]'):
            item = self._parse_job_anchor(
                board_url=url,
                source_name=source_name,
                anchor=anchor,
                section=None,
                team=None,
            )
            if item is not None:
                items.append(item)
            if len(items) >= limit:
                break
        return items


class _BCCParser:
    def __init__(self, *, max_concurrency: int = 5) -> None:
        self._max_concurrency = max_concurrency

    def _job_id(self, url: str) -> str:
        match = _BCC_JOB_ID_RE.search(url)
        return match.group("job_id") if match is not None else url

    async def _parse_anchor(self, client: Any, vacancies_url: str, anchor: Any) -> RawItem | None:
        href = anchor.attributes.get("href")
        if not href:
            return None
        detail_url = urljoin(vacancies_url, href)
        detail_response = await client.get(detail_url, follow_redirects=True)
        detail_parser = LexborHTMLParser(detail_response.text)
        detail_card = detail_parser.css_first(".bg-white.rounded-xl") or detail_parser

        title_node = detail_card.css_first("h1")
        location_node = detail_card.css_first("h1 + div")
        body_node = detail_card.css_first(".text-neutral-700")
        badge_nodes = anchor.css(".rounded-\\[72px\\]")

        title = (
            _clean_text(title_node.text(separator=" ", strip=True))
            if title_node is not None
            else None
        )
        location = (
            _clean_text(location_node.text(separator=" ", strip=True))
            if location_node is not None
            else None
        )
        description = body_node.text(separator="\n", strip=True) if body_node is not None else None
        description_text = _clean_text(description) if description else None
        if not title:
            return None

        badges = list(
            dict.fromkeys(
                _clean_text(node.text(separator=" ", strip=True))
                for node in badge_nodes
                if _clean_text(node.text(separator=" ", strip=True))
            )
        )
        text_parts = [title, location]
        if description_text:
            text_parts.append(description_text)

        return build_raw_item(
            source_kind=SourceKind.CAREER_SITE,
            source_name="BCC",
            external_id=self._job_id(detail_url),
            url=detail_url,
            text="\n".join(part for part in text_parts if part),
            metadata={
                "board_url": vacancies_url,
                "job_url": detail_url,
                "location": location,
                "badges": badges,
                "parser": "bcc",
            },
        )

    async def parse(
        self,
        *,
        client: Any,
        url: str,
        html: str,
        limit: int,
    ) -> list[RawItem]:
        parser = LexborHTMLParser(html)
        list_node = parser.css_first('[data-ajax-partial="career/list"]')
        if list_node is None:
            return []

        anchors = list_node.css('a[href^="https://www.bcc.kz/career/"], a[href^="/career/"]')
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def _load(anchor: Any) -> RawItem | None:
            async with semaphore:
                return await self._parse_anchor(client, url, anchor)

        items = await asyncio.gather(*(_load(anchor) for anchor in anchors[:limit]))
        return [item for item in items if item is not None]


class _YandexJobsParser:
    def _extract_tag_texts(self, card: Any) -> list[str]:
        texts: list[str] = []
        for tag in card.css(".lc-jobs-vacancy-card__tag"):
            text = _clean_text(tag.text(separator=" ", strip=True))
            if text and text not in texts:
                texts.append(text)
        return texts

    async def parse(
        self,
        *,
        client: Any,
        url: str,
        html: str,
        limit: int,
    ) -> list[RawItem]:
        del client
        parser = LexborHTMLParser(html)
        cards = parser.css("span[data-vacancy-card=true]")
        items: list[RawItem] = []

        for card in cards[:limit]:
            href = card.css_first(".lc-jobs-vacancy-card__link")
            if href is None:
                continue
            link = href.attributes.get("href")
            if not link:
                continue
            full_url = urljoin(url, link)
            title_node = card.css_first(".lc-jobs-vacancy-card__header")
            summary_node = card.css_first(".lc-jobs-vacancy-card__description")
            title = _clean_text(title_node.text(separator=" ", strip=True)) if title_node else ""
            summary = (
                _clean_text(summary_node.text(separator=" ", strip=True)) if summary_node else None
            )
            if not title:
                continue
            tags = self._extract_tag_texts(card)
            service = tags[0] if tags else "Yandex"
            metadata = {
                "board_url": url,
                "job_url": full_url,
                "service": service,
                "tags": tags,
                "parser": "yandex_jobs",
            }
            text_parts = [title, summary, *tags]
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name="Yandex",
                    external_id=str(card.attributes.get("data-vacancy-id") or full_url),
                    url=full_url,
                    text="\n".join(part for part in text_parts if part),
                    metadata=metadata,
                )
            )
        return items


class CareerSiteSource:
    def __init__(
        self,
        client: Any,
        site_url: str,
        *,
        limit: int = 100,
        own_client: bool = False,
        parser: _CareerSiteParser | None = None,
    ) -> None:
        self._client = client
        self._site_url = site_url.rstrip("/") + "/"
        self._limit = limit
        self._own_client = own_client
        self._parser = parser

    async def fetch(self) -> AsyncIterator[RawItem]:
        async with _http_session(self._client, own_client=self._own_client) as client:
            response = await client.get(self._site_url, follow_redirects=True)
            parser = self._parser or self._select_parser(url=self._site_url, html=response.text)
            items = await parser.parse(
                client=client,
                url=self._site_url,
                html=response.text,
                limit=self._limit,
            )
            for item in items:
                yield item

    def _select_parser(self, *, url: str, html: str) -> _CareerSiteParser:
        return resolve_career_site_parser(url=url, html=html)  # type: ignore[return-value]


def _is_bcc(url: str, html: str) -> bool:
    del url
    parser = LexborHTMLParser(html)
    return parser.css_first('[data-ajax-partial="career/list"]') is not None


def _is_yandex_jobs(url: str, html: str) -> bool:
    return "yandex.ru/jobs" in url.lower() and 'data-vacancy-card="true"' in html


@register_parser("yandex_jobs", matcher=_is_yandex_jobs)
def _build_yandex_jobs_parser() -> _YandexJobsParser:
    return _YandexJobsParser()


@register_parser("bcc", matcher=_is_bcc)
def _build_bcc_parser() -> _BCCParser:
    return _BCCParser()


@register_source("career_site")
def _build_career_site_source(settings: Settings) -> CareerSiteSource:
    if settings.career_site_url is None:
        msg = "Career site source requires JOB_FTCH_CAREER_SITE_URL."
        raise ValueError(msg)
    timeout = httpx.Timeout(settings.career_site_timeout_seconds, connect=30.0)
    limits = httpx.Limits(
        max_keepalive_connections=settings.career_site_max_keepalive_connections,
        max_connections=settings.career_site_max_connections,
    )
    client = _RetryingHttpClient(
        httpx.AsyncClient(timeout=timeout, limits=limits),
        max_retries=settings.career_site_max_retries,
        retry_delay_seconds=settings.career_site_retry_delay_seconds,
    )
    return CareerSiteSource(
        client,
        settings.career_site_url,
        limit=settings.pipeline_max_items_per_run,
        own_client=True,
        parser=_BCCParser(max_concurrency=settings.career_site_detail_concurrency)
        if urlsplit(settings.career_site_url).hostname in {"www.bcc.kz", "bcc.kz"}
        else None,
    )
