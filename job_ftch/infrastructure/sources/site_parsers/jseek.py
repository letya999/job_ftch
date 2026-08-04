"""Discover original vacancy URLs from the JSeek aggregator."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from selectolax.parser import HTMLParser

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    normalize_search_keywords,
    safe_fetch,
    with_query_params,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from job_ftch.domain.source_spec import CareerSiteSpec


_DOMAIN_PATTERN = r"^https?://(?:www\.)?jseek\.co/(?:[a-z]{2}/)?explore(?:[/?#]|$)"
_UUID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_POSTING_RE = re.compile(
    rf'"id":"(?P<id>{_UUID_RE})","title":(?:"(?P<title>[^"]*)"|null),'
    r'"firstSeenAt":',
    re.IGNORECASE,
)
_ACTION_RE = re.compile(
    r'createServerReference\)?\("(?P<action>[a-f0-9]{32,})"[^)]*"getPostingDetail"',
    re.IGNORECASE | re.DOTALL,
)
_RSC_OBJECT_RE = re.compile(r"^\d+:(?P<payload>\{.*\})$", re.DOTALL)
_NEXT_ROUTER_STATE_TREE = (
    '["",{"children":[["lang","en","d",null],{"children":["(app)",'
    '{"children":["explore",{"children":["__PAGE__",{},null,null,0]},null,null,4]},'
    "null,null,8]},null,null,28]},null,null,8]"
)


def _lightly_unescape_next_payload(text: str) -> str:
    """Undo the quote escaping used in embedded Next/RSC payload snippets."""
    previous = text
    for _ in range(3):
        current = previous.replace('\\"', '"')
        if current == previous:
            return current
        previous = current
    return previous


def _extract_listing_posting_ids(html: str, *, limit: int) -> list[str]:
    unescaped = _lightly_unescape_next_payload(html)
    ids: list[str] = []
    seen: set[str] = set()
    for match in _POSTING_RE.finditer(unescaped):
        posting_id = match.group("id")
        if posting_id in seen:
            continue
        seen.add(posting_id)
        ids.append(posting_id)
        if len(ids) >= limit:
            break
    return ids


def _extract_script_urls(html: str, base_url: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for node in HTMLParser(html).css("script[src]"):
        src = node.attributes.get("src")
        if not src:
            continue
        url = urljoin(base_url, src)
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def _extract_detail_action_id(javascript: str) -> str | None:
    match = _ACTION_RE.search(javascript)
    return match.group("action") if match else None


def _extract_detail_payload(rsc_text: str) -> dict[str, Any] | None:
    for line in rsc_text.splitlines():
        match = _RSC_OBJECT_RE.match(line.strip())
        if not match:
            continue
        try:
            payload = json.loads(match.group("payload"))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("sourceUrl"), str):
            return payload
    match = re.search(r'"sourceUrl":"(?P<url>[^"\\]*(?:\\.[^"\\]*)*)"', rsc_text)
    if match:
        return {"sourceUrl": json.loads(f'"{match.group("url")}"')}
    return None


def _canonical_source_url(raw_url: str) -> str | None:
    try:
        parsed = urlparse(raw_url)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
    ]
    return urlunparse(
        parsed._replace(
            scheme=parsed.scheme.lower(),
            netloc=parsed.netloc.lower(),
            query=urlencode(query, doseq=True),
            fragment="",
        )
    )


class JSeekParser:
    """Treat JSeek as an aggregator and return original vacancy URLs only."""

    domain_pattern = _DOMAIN_PATTERN
    has_custom_parse = True
    supports_discover = True
    supports_search = True
    search_mode = "combined"

    def __init__(self) -> None:
        self._detail_action_id: str | None = None

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=False,
            wait="domcontentloaded",
            include_if_detail_page=False,
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return "jseek"

    def build_search_urls(
        self,
        base_url: str,
        keywords: Sequence[str],
        *,
        limit: int | None = None,
    ) -> list[str]:
        del limit
        normalized = normalize_search_keywords(keywords, cap=8)
        tokens: list[str] = []
        seen: set[str] = set()
        for keyword in normalized:
            for token in re.split(r"[\s,]+", keyword):
                token = token.strip()
                if not token:
                    continue
                key = token.casefold()
                if key in seen:
                    continue
                seen.add(key)
                tokens.append(token)
        if not tokens:
            return []
        return [with_query_params(base_url, {"q": ",".join(tokens)})]

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        limit = spec.limit or 50
        search_query = _search_query_from_url(spec.url)
        if search_query:
            return await self._discover_search_urls(client, spec.url, search_query, limit=limit)

        listing_response = await safe_fetch(client, spec.url)
        listing_url = str(listing_response.url)
        html = listing_response.text
        posting_ids = _extract_listing_posting_ids(html, limit=limit * 4)
        if not posting_ids:
            return []

        action_id = self._detail_action_id or await self._resolve_detail_action_id(
            client, html, listing_url
        )
        if action_id is None:
            return []
        self._detail_action_id = action_id

        urls: list[str] = []
        seen: set[str] = set()
        locale = _locale_from_url(listing_url)
        for posting_id in posting_ids:
            source_url = await self._fetch_source_url(
                client,
                listing_url,
                posting_id,
                locale=locale,
                action_id=action_id,
            )
            if source_url is None or source_url in seen:
                continue
            seen.add(source_url)
            urls.append(source_url)
            if len(urls) >= limit:
                break
        return urls

    async def _discover_search_urls(
        self,
        client: Any,
        referer_url: str,
        search_query: str,
        *,
        limit: int,
    ) -> list[str]:
        key_response = await client.get("https://jseek.co/api/typesense-key", follow_redirects=True)
        if hasattr(key_response, "raise_for_status"):
            key_response.raise_for_status()
        key_payload = key_response.json()
        api_key = str(key_payload.get("apiKey") or "").strip()
        host = str(key_payload.get("host") or "typesense.colophon-group.org").strip()
        protocol = str(key_payload.get("protocol") or "https").strip()
        port = int(key_payload.get("port") or 443)
        if not api_key or not host:
            return []

        base_url = f"{protocol}://{host}"
        if (protocol, port) not in {("https", 443), ("http", 80)}:
            base_url = f"{base_url}:{port}"
        response = await client.get(
            f"{base_url}/collections/job_posting/documents/search",
            headers={
                "x-typesense-api-key": api_key,
                "referer": referer_url,
            },
            params={
                "q": search_query,
                "query_by": "title",
                "filter_by": "is_active:true && has_content:!=false && locales:[en,_none]",
                "sort_by": "_text_match:desc,first_seen_at:desc",
                "group_by": "company_id",
                "group_limit": str(max(limit, 10)),
                "per_page": str(min(max(limit, 10), 50)),
                "page": "1",
                "typo_tokens_threshold": "1",
                "drop_tokens_threshold": "1",
            },
        )
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        payload = response.json()
        return _source_urls_from_typesense_payload(payload, limit=limit)

    async def _resolve_detail_action_id(
        self,
        client: Any,
        html: str,
        listing_url: str,
    ) -> str | None:
        for script_url in _extract_script_urls(html, listing_url):
            response = await client.get(script_url, follow_redirects=True)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            action_id = _extract_detail_action_id(response.text)
            if action_id is not None:
                return action_id
        return None

    async def _fetch_source_url(
        self,
        client: Any,
        listing_url: str,
        posting_id: str,
        *,
        locale: str,
        action_id: str,
    ) -> str | None:
        detail_url = _detail_url_for_posting(listing_url, posting_id)
        response = await client.post(
            detail_url,
            headers={
                "accept": "text/x-component",
                "content-type": "text/plain;charset=UTF-8",
                "next-action": action_id,
                "next-router-state-tree": _NEXT_ROUTER_STATE_TREE,
                "referer": detail_url,
            },
            content=json.dumps(
                [{"postingId": posting_id, "locale": locale}],
                separators=(",", ":"),
            ),
        )
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        payload = _extract_detail_payload(response.text)
        if payload is None:
            return None
        source_url = payload.get("sourceUrl")
        if not isinstance(source_url, str):
            return None
        return _canonical_source_url(source_url)


def _locale_from_url(url: str) -> str:
    path_parts = [part for part in urlparse(url).path.split("/") if part]
    if path_parts and re.fullmatch(r"[a-z]{2}", path_parts[0], re.IGNORECASE):
        return path_parts[0].lower()
    return "en"


def _detail_url_for_posting(listing_url: str, posting_id: str) -> str:
    parsed = urlparse(listing_url)
    return urlunparse(parsed._replace(query=urlencode({"show": posting_id}), fragment=""))


def _search_query_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    raw = str(query.get("q") or "").strip()
    if not raw:
        return None
    tokens = [token for token in re.split(r"[\s,]+", raw) if token]
    return " ".join(tokens) if tokens else None


def _source_urls_from_typesense_payload(payload: dict[str, Any], *, limit: int) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    groups = payload.get("grouped_hits")
    if not isinstance(groups, list):
        return urls
    for group in groups:
        if not isinstance(group, dict):
            continue
        hits = group.get("hits")
        if not isinstance(hits, list):
            continue
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            document = hit.get("document")
            if not isinstance(document, dict):
                continue
            source_url = document.get("source_url")
            if not isinstance(source_url, str):
                continue
            canonical_url = _canonical_source_url(source_url)
            if canonical_url is None or canonical_url in seen:
                continue
            seen.add(canonical_url)
            urls.append(canonical_url)
            if len(urls) >= limit:
                return urls
    return urls


register_site_parser(
    "jseek",
    domain_pattern=JSeekParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:jseek.co",
        has_stable_url=True,
        supports_ordered_head=True,
        can_detect_freshness_without_snapshot=True,
        ordered_by_newest=True,
        requires_full_snapshot=False,
        rationale="JSeek exposes posting ids and structured detail payloads with original sourceUrl.",
    ),
)(JSeekParser)
