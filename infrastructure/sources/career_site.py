"""Career-site source adapters with parser auto-detection."""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser

from domain import RawItem, SourceKind
from infrastructure.sources.raw_item_factory import build_raw_item

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


_GREENHOUSE_JOB_ID_RE = re.compile(r"/jobs/(?P<job_id>\d+)")
_BCC_JOB_ID_RE = re.compile(r"/career/(?P<job_id>\d+)/?$")


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
    def _job_id(self, url: str) -> str:
        match = _BCC_JOB_ID_RE.search(url)
        return match.group("job_id") if match is not None else url

    async def _parse_anchor(self, client: Any, vacancies_url: str, anchor: Any) -> RawItem | None:
        href = anchor.attributes.get("href")
        if not href:
            return None
        detail_url = urljoin(vacancies_url, href)
        detail_response = await client.get(detail_url, follow_redirects=True)
        detail_response.raise_for_status()
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

        items: list[RawItem] = []
        anchors = list_node.css('a[href^="https://www.bcc.kz/career/"], a[href^="/career/"]')
        for anchor in anchors[:limit]:
            item = await self._parse_anchor(client, url, anchor)
            if item is not None:
                items.append(item)
        return items


class CareerSiteSource:
    def __init__(
        self,
        client: Any,
        site_url: str,
        *,
        limit: int = 100,
        own_client: bool = False,
    ) -> None:
        self._client = client
        self._site_url = site_url.rstrip("/") + "/"
        self._limit = limit
        self._own_client = own_client

    async def fetch(self) -> AsyncIterator[RawItem]:
        async with _http_session(self._client, own_client=self._own_client) as client:
            response = await client.get(self._site_url, follow_redirects=True)
            response.raise_for_status()
            parser = self._select_parser(url=self._site_url, html=response.text)
            items = await parser.parse(
                client=client,
                url=self._site_url,
                html=response.text,
                limit=self._limit,
            )
            for item in items:
                yield item

    def _select_parser(self, *, url: str, html: str) -> _CareerSiteParser:
        lowered_url = url.lower()
        if "greenhouse.io" in lowered_url:
            return _GreenhouseParser()
        parser = LexborHTMLParser(html)
        if parser.css_first('[data-ajax-partial="career/list"]') is not None:
            return _BCCParser()
        msg = f"Unsupported career site layout for URL: {url}"
        raise ValueError(msg)
