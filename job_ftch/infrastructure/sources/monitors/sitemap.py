"""XML sitemap monitor ported from jobseek.

Gzip handling and bad-response fixing ported from Botasaurus
(omkarcloud/botasaurus, sitemap_parser_utils.py).
"""

from __future__ import annotations

import gzip
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlencode, urlparse

import defusedxml.ElementTree as ET
import structlog

from job_ftch.application.registry import register_monitor

if TYPE_CHECKING:
    import httpx

logger = structlog.get_logger("job_ftch.monitors.sitemap")

MAX_URLS = 50_000
NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
_JOB_KEYWORDS = ("job", "career", "posting", "position", "vacancy", "opening")

_SITEMAP_HEADERS = {
    "User-Agent": "jobseek-crawler (+https://jseek.co/)",
    "Accept": "application/xml,text/xml,*/*;q=0.8",
}


class SitemapDiscoveryError(Exception):
    """Raised when no sitemap URL can be found for a board."""


def _strip_utm(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.query:
        return url
    params = parse_qs(parsed.query, keep_blank_values=True)
    filtered = {k: v for k, v in params.items() if not k.startswith("utm_")}
    if not filtered:
        return parsed._replace(query="").geturl()
    return parsed._replace(query=urlencode(filtered, doseq=True)).geturl()


def _detect_ns(root: ET.Element) -> str:
    tag = root.tag
    if tag.startswith("{"):
        return str(tag[: tag.index("}") + 1])
    return NS


def _extract_urls(root: ET.Element) -> list[str]:
    ns = _detect_ns(root)
    urls: list[str] = []
    for url_el in root.findall(f"{ns}url"):
        loc = url_el.find(f"{ns}loc")
        if loc is not None and loc.text:
            urls.append(_strip_utm(loc.text.strip()))
    if not urls:
        for url_el in root.findall("url"):
            loc = url_el.find("loc")
            if loc is not None and loc.text:
                urls.append(_strip_utm(loc.text.strip()))
    return urls


def _is_sitemap_index(root: ET.Element) -> bool:
    tag = root.tag.lower()
    return "sitemapindex" in tag


def _extract_child_sitemaps(root: ET.Element) -> list[str]:
    ns = _detect_ns(root)
    urls: list[str] = []
    for el in root.findall(f"{ns}sitemap"):
        loc = el.find(f"{ns}loc")
        if loc is not None and loc.text:
            urls.append(loc.text.strip())
    if not urls:
        for el in root.findall("sitemap"):
            loc = el.find("loc")
            if loc is not None and loc.text:
                urls.append(loc.text.strip())
    return urls


def _is_job_related(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(kw in path for kw in _JOB_KEYWORDS)


def _is_gzip_response(url: str, content_type: str) -> bool:
    """Detect gzip by content-type header or .gz URL extension (Botasaurus pattern)."""
    if "gzip" in content_type.lower():
        return True
    path = urlparse(url).path.lower()
    return path.endswith(".gz")


def _gunzip_data(data: bytes) -> str:
    """Decompress gzipped bytes to string (Botasaurus pattern).

    Raises ValueError on invalid gzip data instead of silently swallowing.
    """
    if not data:
        raise ValueError("empty response body")
    try:
        decompressed = gzip.decompress(data)
    except Exception as exc:
        raise ValueError(f"gzip decompression failed: {exc}") from exc
    return decompressed.decode("utf-8-sig", errors="replace")


def _fix_bad_sitemap_response(text: str) -> str:
    """Strip leading junk before the first '<' character (Botasaurus pattern).

    Some proxies/CDNs prepend garbage to sitemap XML.
    """
    if not text:
        return text
    idx = text.find("<")
    if idx == -1:
        return text
    return text[idx:]


def _parse_xml(text: str) -> ET.Element | None:
    """Parse XML text, trying to fix bad responses on failure."""
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        fixed = _fix_bad_sitemap_response(text)
        if fixed != text:
            try:
                return ET.fromstring(fixed)
            except ET.ParseError:
                pass
    return None


async def _try_fetch_xml(url: str, client: httpx.AsyncClient) -> ET.Element | None:
    try:
        resp = await client.get(url, headers=_SITEMAP_HEADERS, follow_redirects=True)
        if resp.status_code != 200:
            return None

        content_type = resp.headers.get("content-type", "")

        # Handle gzip-compressed sitemaps (Botasaurus pattern)
        if _is_gzip_response(url, content_type):
            try:
                text = _gunzip_data(resp.content)
            except ValueError:
                return None
        else:
            text = resp.text

        # Validate it looks like XML
        stripped = text.lstrip()
        if not stripped.startswith("<?xml") and not stripped.startswith("<"):
            return None

        return _parse_xml(text)
    except Exception as exc:
        logger.debug("sitemap.fetch_xml_failed", url=url, error=str(exc))
        return None


def _walk_up_candidates(board_url: str) -> list[str]:
    parsed = urlparse(board_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")

    candidates: list[str] = []
    candidates.append(f"{origin}{path}/sitemap.xml")

    while "/" in path:
        path = path.rsplit("/", 1)[0]
        if path:
            candidates.append(f"{origin}{path}/sitemap.xml")

    root = f"{origin}/sitemap.xml"
    if root not in candidates:
        candidates.append(root)

    return candidates


def _common_nonstandard_candidates(board_url: str) -> list[str]:
    parsed = urlparse(board_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return [
        f"{origin}/sitemaps/sitemapIndex",
        f"{origin}/sitemap/sitemap.xml",
        f"{origin}/sitemaps/sitemap.xml",
    ]


async def _parse_robots_sitemaps(board_url: str, client: httpx.AsyncClient) -> list[str]:
    """Discover sitemap URLs from robots.txt (Botasaurus-inspired pattern).

    Handles case-insensitive Sitemap: directives and resolves relative URLs
    against the board's origin, matching the robustness of Botasaurus's
    parse_sitemaps_from_robots_txt().
    """
    import re as _re

    parsed = urlparse(board_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        resp = await client.get(robots_url, headers=_SITEMAP_HEADERS, follow_redirects=True)
        if resp.status_code != 200:
            return []
    except Exception as exc:
        logger.debug("sitemap.robots_fetch_failed", url=robots_url, error=str(exc))
        return []

    origin = f"{parsed.scheme}://{parsed.netloc}"
    seen: dict[str, bool] = {}  # ordered set for dedup
    for line in resp.text.splitlines():
        line = line.strip()
        match = _re.match(r"^sitemap:\s*(.+?)$", line, flags=_re.IGNORECASE)
        if not match:
            continue
        url = match.group(1).strip()
        if not url:
            continue
        # Resolve relative URLs against origin (Botasaurus clean_url pattern)
        url_parsed = urlparse(url)
        if not url_parsed.scheme:
            url = f"{origin}/{url.lstrip('/')}"
        seen[url] = True

    return list(seen.keys())


async def _resolve_sitemap_index(
    root: ET.Element,
    client: httpx.AsyncClient,
    *,
    seen: set[str] | None = None,
) -> list[ET.Element]:
    children = _extract_child_sitemaps(root)
    if not children:
        return []
    job_children = [u for u in children if _is_job_related(u)]
    targets = job_children if job_children else children
    if seen is None:
        seen = set()
    results: list[ET.Element] = []
    for target in targets:
        if target in seen:
            continue
        seen.add(target)
        child_root = await _try_fetch_xml(target, client)
        if child_root is None:
            continue
        if _is_sitemap_index(child_root):
            results.extend(await _resolve_sitemap_index(child_root, client, seen=seen))
        else:
            results.append(child_root)
    return results


async def _discover_sitemap(
    board_url: str, client: httpx.AsyncClient
) -> tuple[str, list[ET.Element]]:
    all_candidates = (
        _walk_up_candidates(board_url)
        + _common_nonstandard_candidates(board_url)
        + await _parse_robots_sitemaps(board_url, client)
    )

    for candidate in all_candidates:
        root = await _try_fetch_xml(candidate, client)
        if root is not None:
            if _is_sitemap_index(root):
                children = await _resolve_sitemap_index(root, client)
                if children:
                    return candidate, children
            else:
                return candidate, [root]

    raise SitemapDiscoveryError(f"No sitemap found for {board_url}")


async def discover(
    spec: Any, client: httpx.AsyncClient, auth: Any = None
) -> tuple[set[str], str | None]:
    board_url = spec.url
    cached_sitemap = spec.monitor_config.get("sitemap_url")
    new_sitemap_url: str | None = None

    if cached_sitemap:
        root = await _try_fetch_xml(cached_sitemap, client)
        if root is None:
            sitemap_url, roots = await _discover_sitemap(board_url, client)
            new_sitemap_url = sitemap_url
        else:
            if _is_sitemap_index(root):
                roots = await _resolve_sitemap_index(root, client)
            else:
                roots = [root]
    else:
        sitemap_url, roots = await _discover_sitemap(board_url, client)
        new_sitemap_url = sitemap_url

    urls: list[str] = []
    for r in roots:
        urls.extend(_extract_urls(r))

    if len(urls) > MAX_URLS:
        # Sitemaps are normally alphabetical.  Taking the first N therefore
        # systematically drops vacancies on large corporate sites (for
        # example when product/localization URLs fill the beginning).  Keep
        # likely job locators first; final detail validation remains in the
        # source layer and this is only a bounded inventory frontier.
        urls = sorted(urls, key=lambda candidate: (not _is_job_related(candidate), candidate))[
            :MAX_URLS
        ]

    return set(urls), new_sitemap_url


async def can_handle(url: str, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
    if client is None:
        return None
    try:
        sitemap_url, roots = await _discover_sitemap(url, client)
        url_count = sum(len(_extract_urls(r)) for r in roots)
        return {"sitemap_url": sitemap_url, "urls": url_count}
    except Exception as exc:
        logger.debug("sitemap.can_handle_failed", url=url, error=str(exc))
        return None


register_monitor(
    "sitemap",
    discover,
    cost=20,
    rich=False,
    can_handle=can_handle,
    scraper_chain=("json-ld", "embedded", "nextdata", "dom", "maintext"),
)
