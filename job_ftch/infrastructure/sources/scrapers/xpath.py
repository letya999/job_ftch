"""XPath scraper — extracts job data using XPath expressions.

Targets pages where CSS selectors are awkward (Workday, SuccessFactors).
Requires the [extraction] extra (lxml).

Registered after json_ld and embedded, before maintext. Activates only
when config contains an "xpath" key with extraction rules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from job_ftch.application.registry import register_scraper
from job_ftch.domain.site_models import ScrapedPostingPayload
from job_ftch.infrastructure.sources.http_retry import fetch_with_retry

if TYPE_CHECKING:
    import httpx

logger = structlog.get_logger("job_ftch.scrapers.xpath")

try:
    from lxml import etree

    _LXML_AVAILABLE = True
except ImportError:
    _LXML_AVAILABLE = False

try:
    from parsel import Selector

    _PARSEL_AVAILABLE = True
except ImportError:
    Selector = None  # type: ignore[assignment,misc]
    _PARSEL_AVAILABLE = False


_DEFAULT_RULES: dict[str, str] = {
    "title": "//h1[1]//text()",
    "description": "//div[contains(@class,'job-description') or contains(@class,'jobDescription') or contains(@class,'posting-description')]//text()",
    "location": "//*[contains(@class,'location') or contains(@class,'Location')]//text()",
    "employment_type": "//*[contains(@class,'employment') or contains(@class,'job-type')]//text()",
}


def _extract_text(tree: Any, xpath_expr: str) -> str:
    """Extract and join text from an XPath expression."""
    try:
        if _PARSEL_AVAILABLE and hasattr(tree, "xpath"):
            selection = tree.xpath(xpath_expr)
            getall = getattr(selection, "getall", None)
            nodes = getall() if callable(getall) else selection
        else:
            nodes = tree.xpath(xpath_expr)
    except Exception:
        return ""
    texts: list[str] = []
    for node in nodes:
        if isinstance(node, str):
            texts.append(node.strip())
        elif hasattr(node, "text_content"):
            texts.append(node.text_content().strip())
        elif hasattr(node, "strip"):
            texts.append(str(node).strip())
    return " ".join(t for t in texts if t)


def can_handle(url: str, config: dict[str, Any]) -> bool:
    """Activate only when config has xpath rules or lxml is available and page is a target."""
    if not (_PARSEL_AVAILABLE or _LXML_AVAILABLE):
        return False
    return "xpath" in config or "xpath_rules" in config


async def scrape(
    url: str,
    config: dict[str, Any],
    http: httpx.AsyncClient,
) -> ScrapedPostingPayload | None:
    """Extract job data using XPath expressions."""
    if not (_PARSEL_AVAILABLE or _LXML_AVAILABLE):
        return None

    rules = config.get("xpath_rules") or config.get("xpath") or _DEFAULT_RULES
    if isinstance(rules, str):
        return None

    try:
        html = config.get("prefetched_html")
        if not isinstance(html, str):
            resp = await fetch_with_retry(http, url)
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:
        logger.warning("xpath.fetch_failed", url=url, error=str(exc))
        return None

    try:
        if _PARSEL_AVAILABLE and Selector is not None:
            tree = Selector(text=html, type="html")
        else:
            parser = etree.HTMLParser(encoding="utf-8")
            tree = etree.fromstring(html.encode("utf-8", errors="replace"), parser)
    except Exception as exc:
        logger.warning("xpath.parse_failed", url=url, error=str(exc))
        return None

    title = _extract_text(tree, rules.get("title", _DEFAULT_RULES["title"]))
    description = _extract_text(tree, rules.get("description", _DEFAULT_RULES["description"]))

    if not title and not description:
        return None

    location_text = _extract_text(tree, rules.get("location", _DEFAULT_RULES.get("location", "")))
    employment_type = _extract_text(
        tree, rules.get("employment_type", _DEFAULT_RULES.get("employment_type", ""))
    )

    locations: list[str] = []
    if location_text:
        locations = [loc.strip() for loc in location_text.split(",") if loc.strip()]

    return ScrapedPostingPayload(
        title=title,
        description=description,
        locations=locations,
        employment_type=employment_type or None,
    )


if _PARSEL_AVAILABLE or _LXML_AVAILABLE:
    register_scraper("xpath", scrape, can_handle=can_handle)
