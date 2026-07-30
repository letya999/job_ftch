"""Main-text fallback scraper using trafilatura.

Extracts the primary article body from HTML without CSS selectors. Intended as
a last-resort layer that runs after json-ld and dom scrapers have declined a
page. Returns a minimal ScrapedPostingPayload with title and description; all
other structured fields are left None (salary, locations, etc.) because
trafilatura does not parse job-specific schema.

Requires the [extraction] extra (trafilatura>=1.8). Degrades silently to None
when the extra is absent so the core package remains importable without it.

Chain position: registered with ``can_handle`` that intentionally yields a weak
signal — it returns {} only when trafilatura can extract a body of at least
``_MIN_CHARS`` characters. The scraper registry tries scrapers in registration
order; this module must be imported AFTER json_ld and dom so it sits last in
the chain.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import structlog

from job_ftch.application.registry import register_scraper
from job_ftch.domain.site_models import ScrapedPostingPayload
from job_ftch.infrastructure.sources.http_retry import fetch_with_retry

if TYPE_CHECKING:
    import httpx

logger = structlog.get_logger(__name__)

_MIN_CHARS = 120
_ARTICLE_OPEN_GRAPH_RE = re.compile(
    r"<meta\b[^>]+(?:property|name)=[\"']og:type[\"'][^>]+content=[\"']article[\"']"
    r"|<meta\b[^>]+content=[\"']article[\"'][^>]+(?:property|name)=[\"']og:type[\"']",
    re.IGNORECASE,
)

try:
    from trafilatura import bare_extraction  # type: ignore[import-untyped]

    _TRAFILATURA_AVAILABLE = True
except ImportError:
    _TRAFILATURA_AVAILABLE = False


def _normalize_title(text: str) -> str:
    cleaned = " ".join(text.split())
    for sep in (" | ", " — ", " - "):
        if sep in cleaned:
            left, _, _ = cleaned.partition(sep)
            if left.strip():
                return left.strip()
    return cleaned


def parse_html(html: str) -> ScrapedPostingPayload | None:
    """Extract title + description via trafilatura boilerplate stripping.

    Returns None when trafilatura is not installed, when extraction fails,
    or when the extracted body is shorter than _MIN_CHARS (avoids emitting
    nav-only / empty pages as vacancies).
    """
    if not _TRAFILATURA_AVAILABLE:
        return None

    # This scraper has no job-specific schema to validate the result.  Do not
    # use it to convert explicitly labelled editorial content into a vacancy.
    if _ARTICLE_OPEN_GRAPH_RE.search(html):
        return None

    try:
        result = bare_extraction(
            html,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
            with_metadata=True,
        )
    except Exception as exc:
        logger.debug("maintext.extract_error", error=str(exc))
        return None

    if not result:
        return None

    if isinstance(result, dict):
        text = result.get("text") or ""
        raw_title = result.get("title") or ""
    else:
        text = getattr(result, "text", "") or ""
        raw_title = getattr(result, "title", "") or ""

    if len(text) < _MIN_CHARS:
        return None

    title = _normalize_title(raw_title) if raw_title else None

    return ScrapedPostingPayload(title=title, description=text)


def can_handle(htmls: list[str]) -> dict[str, Any] | None:
    """Return {} only when trafilatura extracts a plausible body.

    Kept deliberately weak so json-ld and dom scrapers win when they match.
    """
    if not _TRAFILATURA_AVAILABLE:
        return None

    for html in htmls:
        result = parse_html(html)
        if result and result.description:
            return {}

    return None


async def scrape(
    url: str,
    config: dict[str, Any],
    http: httpx.AsyncClient,
) -> ScrapedPostingPayload | None:
    try:
        html = config.get("prefetched_html")
        if not isinstance(html, str):
            resp = await fetch_with_retry(http, url)
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:
        logger.error("maintext.fetch_failed", url=url, error=str(exc))
        return None

    result = parse_html(html)
    if result:
        logger.debug("maintext.hit", url=url, desc_len=len(result.description or ""))
    return result


register_scraper(
    "maintext",
    scrape,
    can_handle=can_handle,
    requires_extras=("extraction",),
)
