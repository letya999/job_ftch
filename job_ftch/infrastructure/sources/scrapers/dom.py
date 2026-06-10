"""DOM scraper — extracts job data using step-based extraction.

Uses the step-based extraction engine from ``dom_utils.py`` to pull
structured fields from the HTML.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import structlog

from job_ftch.application.registry import register_scraper
from job_ftch.infrastructure.sources.dom_utils import flatten, walk_steps
from job_ftch.infrastructure.sources.site_models import ScrapedPostingPayload

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
]


def _heuristic_steps(elements: list[dict]) -> list[dict] | None:
    """Generate heuristic extraction steps from flattened elements."""
    if not elements:
        return None

    # Find first h1 — title
    h1_idx = None
    for i, el in enumerate(elements):
        if el["tag"] == "h1":
            h1_idx = i
            break

    if h1_idx is None:
        return None

    steps: list[dict] = [{"tag": "h1", "field": "title"}]

    # Description: content after h1, stop at known marker
    desc_step: dict = {
        "tag": "h1",
        "offset": 1,
        "field": "description",
        "html": True,
        "optional": True,
    }

    # Look for a stop marker in elements after h1
    for i in range(h1_idx + 1, len(elements)):
        text = elements[i]["text"]
        for marker in _STOP_MARKERS:
            if marker.lower() in text.lower() and len(text) < 60:
                desc_step["stop"] = marker
                break
        if "stop" in desc_step:
            break

    # If no stop marker found, use stop_count based on remaining content
    if "stop" not in desc_step:
        remaining = len(elements) - h1_idx - 1
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


def can_handle(htmls: list[str]) -> dict | None:
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

    # Validate h1 exists on other pages too
    h1_found = 0
    for html in htmls:
        elements = flatten(html)
        if any(el["tag"] == "h1" for el in elements):
            h1_found += 1

    if h1_found < len(htmls) / 2:
        return None

    return {"steps": best_steps}


def _map_to_payload(raw: dict[str, Any]) -> ScrapedPostingPayload:
    """Map extraction result dict to a ScrapedPostingPayload."""
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
            kwargs[key] = value
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


def _fragment_start(url: str, elements: list[dict]) -> int:
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
    config: dict,
    http: httpx.AsyncClient,
) -> ScrapedPostingPayload | None:
    """Extract job data using step-based extraction."""
    steps = config.get("steps")
    if not steps:
        logger.warning("dom.no_steps", url=url)
        return None

    try:
        resp = await http.get(url, follow_redirects=True)
        resp.raise_for_status()
        html = resp.text
    except Exception as exc:
        logger.error("dom.fetch_failed", url=url, error=str(exc))
        return None

    elements = flatten(html)
    start = _fragment_start(url, elements)
    raw, _ = walk_steps(elements, steps, start=start)
    return _map_to_payload(raw)


register_scraper("dom", scrape, can_handle=can_handle)
