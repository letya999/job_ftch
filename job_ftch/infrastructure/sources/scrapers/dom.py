"""DOM scraper — extracts job data using step-based extraction.

Uses the step-based extraction engine from ``dom_utils.py`` to pull
structured fields from the HTML.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlparse

import structlog

from job_ftch.application.registry import register_scraper
from job_ftch.domain.site_models import ScrapedPostingPayload
from job_ftch.infrastructure.sources.dom_utils import flatten, walk_steps
from job_ftch.infrastructure.sources.http_retry import fetch_with_retry

if TYPE_CHECKING:
    import httpx

logger = structlog.get_logger("job_ftch.scrapers.dom")

# ── Heuristic stop markers ────────────────────────────────────────────

_STOP_MARKERS = [
    "Apply",
    "Requirements",
    "Qualifications",
    "Back",
    "Submit",
    "Similar",
    "Share",
    "Related",
    "Откликнуться",
    "Похожие",
    "Поделиться",
    "Назад",
    "Отправить",
]


def _normalize_title(text: str) -> str:
    cleaned = " ".join(text.split())
    for separator in (" | ", " — ", " - "):
        if separator in cleaned:
            left, _, _ = cleaned.partition(separator)
            if left.strip():
                return left.strip()
    return cleaned


_GENERIC_SECTION_HEADINGS = frozenset(
    {
        "описание",
        "обязанности",
        "требования",
        "description",
        "responsibilities",
        "requirements",
    }
)
_OG_TITLE_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:title["\'][^>]*content=["\']([^"\']+)["\']'
    r"|<meta[^>]+content=[\"']([^\"']+)[\"'][^>]*(?:property|name)=[\"']og:title[\"']",
    re.IGNORECASE,
)


def _is_generic_section_heading(text: str | None) -> bool:
    if not text:
        return True
    return " ".join(text.split()).casefold() in _GENERIC_SECTION_HEADINGS


def _title_from_url_slug(url: str) -> str | None:
    slug = unquote(urlparse(url).path.rstrip("/").rsplit("/", 1)[-1])
    if not slug or slug.isdigit():
        return None
    cleaned = " ".join(slug.replace("-", " ").replace("_", " ").split())
    return cleaned or None


def _heuristic_steps(elements: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Generate heuristic extraction steps from flattened elements."""
    if not elements:
        return None

    # Prefer an in-document heading over ``<title>``.  A document title is
    # normally followed by the global header/navigation in flattened order,
    # so using it as the description anchor can stop immediately on menu copy
    # (for example a "Back" item) before the actual posting body.  A number
    # of CMS job pages use h2 rather than h1 for their posting title.
    # Skip generic section headings such as «Описание» — T-Bank uses that as
    # the first h1/h2, with the real job title later or in og:title.
    anchor_idx = None
    anchor_tag = None
    for heading_tag in ("h1", "h2"):
        for i, el in enumerate(elements):
            if el["tag"] == heading_tag and not _is_generic_section_heading(el.get("text")):
                anchor_idx = i
                anchor_tag = heading_tag
                break
        if anchor_idx is not None:
            break

    if anchor_idx is None:
        for i, el in enumerate(elements):
            if el["tag"] == "title" and not _is_generic_section_heading(el.get("text")):
                anchor_idx = i
                anchor_tag = "title"
                break

    if anchor_idx is None or anchor_tag is None:
        return None

    steps: list[dict[str, Any]] = [{"tag": anchor_tag, "field": "title"}]

    # Description: content after title anchor, stop at known marker.
    desc_step: dict[str, Any] = {
        "tag": anchor_tag,
        # The title step advances the extraction cursor past the heading.  A
        # description range must deliberately re-find that same heading,
        # otherwise it searches only for a *second* h1/h2 and usually returns
        # no body at all (or, worse, starts at an unrelated later heading).
        "from": 0,
        "offset": 1,
        "field": "description",
        "html": True,
        "optional": True,
    }

    # Look for a stop marker in elements after the anchor.
    for i in range(anchor_idx + 1, len(elements)):
        text = elements[i]["text"]
        for marker in _STOP_MARKERS:
            if marker.lower() in text.lower() and len(text) < 60:
                desc_step["stop"] = marker
                break
        if "stop" in desc_step:
            break

    # If no stop marker found, use stop_count based on remaining content
    if "stop" not in desc_step:
        remaining = len(elements) - anchor_idx - 1
        desc_step["stop_count"] = min(remaining, 50)

    steps.append(desc_step)

    # Location: look for an element with "location" in its text
    for el in elements:
        text_lower = el["text"].lower()
        if "location" in text_lower and len(el["text"]) < 40:
            steps.append(
                {
                    "text": "Location",
                    "offset": 1,
                    "field": "location",
                    "optional": True,
                    "from": 0,
                }
            )
            break

    return steps


def can_handle(htmls: list[str]) -> dict[str, Any] | None:
    """Generate heuristic extraction steps from multiple page HTMLs."""
    best_steps = None

    for html in htmls:
        elements = flatten(html)
        if not elements:
            continue
        steps = _heuristic_steps(elements)
        if steps:
            best_steps = steps
            break

    if not best_steps:
        return None

    anchor_tag = best_steps[0].get("tag")
    anchor_found = 0
    for html in htmls:
        elements = flatten(html)
        if any(el["tag"] == anchor_tag for el in elements):
            anchor_found += 1

    if anchor_found < len(htmls) / 2:
        return None

    return {"steps": best_steps}


def _map_to_payload(raw: dict[str, Any]) -> ScrapedPostingPayload:
    """Map extraction result dict[str, Any] to a ScrapedPostingPayload."""
    kwargs: dict[str, Any] = {}
    metadata: dict[str, Any] = {}
    extras: dict[str, Any] = {}

    for key, value in raw.items():
        if value is None:
            continue
        if key.startswith("metadata."):
            metadata[key.removeprefix("metadata.")] = value
        elif key in (
            "title",
            "description",
            "employment_type",
            "job_location_type",
            "date_posted",
            "base_salary",
            "language",
        ):
            kwargs[key] = (
                _normalize_title(value) if key == "title" and isinstance(value, str) else value
            )
        elif key == "location" or key == "locations":
            kwargs["locations"] = value if isinstance(value, list) else [value]
        elif key in ("qualifications", "responsibilities", "skills", "requirements", "benefits"):
            extras[key] = [value] if isinstance(value, str) else value
        else:
            metadata[key] = value

    if metadata:
        kwargs["metadata"] = metadata
    if extras:
        kwargs["extras"] = extras

    return ScrapedPostingPayload(**kwargs)


def _fragment_start(url: str, elements: list[dict[str, Any]]) -> int:
    """Return the element index matching the URL fragment, or 0."""
    fragment = urlparse(url).fragment
    if not fragment:
        return 0
    for i, el in enumerate(elements):
        if el.get("attrs", {}).get("id") == fragment:
            return i
    return 0


async def scrape(
    url: str,
    config: dict[str, Any],
    http: httpx.AsyncClient,
) -> ScrapedPostingPayload | None:
    """Extract job data using step-based extraction."""
    steps = config.get("steps")
    if not steps:
        logger.warning("dom.no_steps", url=url)
        return None

    try:
        html = config.get("prefetched_html")
        if not isinstance(html, str):
            resp = await fetch_with_retry(http, url)
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:
        logger.error("dom.fetch_failed", url=url, error=str(exc))
        return None

    elements = flatten(html)
    start = _fragment_start(url, elements)
    raw, _ = walk_steps(elements, steps, start=start)
    payload = _map_to_payload(raw)
    if payload.title and not _is_generic_section_heading(payload.title):
        return payload
    og_match = _OG_TITLE_RE.search(html)
    og_title = None
    if og_match:
        og_title = (og_match.group(1) or og_match.group(2) or "").strip() or None
    for candidate in (
        og_title,
        next((el["text"] for el in elements if el["tag"] == "title"), None),
    ):
        if candidate and not _is_generic_section_heading(candidate):
            payload.title = _normalize_title(candidate)
            return payload
    slug = _title_from_url_slug(url)
    if slug and not _is_generic_section_heading(slug):
        payload.title = _normalize_title(slug)
    return payload


register_scraper("dom", scrape, can_handle=can_handle)
