"""Embedded data scraper ported from jobseek."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_scraper
from job_ftch.infrastructure.sources.nextdata_utils import (
    extract_field,
    extract_next_data,
    extract_phenom_canvas_data,
    extract_react_router_data,
    extract_rsc_data,
    resolve_path,
)
from job_ftch.infrastructure.sources.site_models import ScrapedPostingPayload

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger("job_ftch.scrapers.embedded")

_TITLE_KEYS = {"title", "name", "jobTitle", "job_title", "position"}
_DESC_KEYS = {"description", "content", "descriptionHtml", "body", "jobDescription"}


def _map_to_payload(raw: dict[str, Any]) -> ScrapedPostingPayload:
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
        elif key == "locations":
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


def _find_job_object(data: Any, path: str) -> tuple[str | None, dict | None]:
    """Recursively search for an object that looks like a job posting."""
    if not isinstance(data, dict):
        return None, None

    # Check if this object itself is a job
    keys = set(data.keys())
    if _TITLE_KEYS.intersection(keys) and _DESC_KEYS.intersection(keys):
        return path, data

    # Recurse into children
    for key, val in data.items():
        if isinstance(val, dict):
            new_path = f"{path}.{key}" if path else key
            found_path, found_obj = _find_job_object(val, new_path)
            if found_obj:
                return found_path, found_obj
    return None, None


def _auto_map_fields(obj: dict[str, Any]) -> dict[str, str]:
    """Generate a field mapping for a job object."""
    mapping = {}
    keys = set(obj.keys())

    for target, source_keys in [
        ("title", _TITLE_KEYS),
        ("description", _DESC_KEYS),
    ]:
        for sk in source_keys:
            if sk in keys:
                mapping[target] = sk
                break

    if "location" in keys:
        mapping["locations"] = "location"
    elif "locations" in keys:
        mapping["locations"] = "locations"

    return mapping


def can_handle(htmls: list[str]) -> dict | None:
    """Detect embedded JSON with job objects across multiple pages."""
    for html in htmls:
        # Try different sources
        for extractor in [extract_next_data, extract_react_router_data, extract_phenom_canvas_data]:
            try:
                data = extractor(html)
                if data:
                    path, obj = _find_job_object(data, "")
                    if obj:
                        fields = _auto_map_fields(obj)
                        if fields:
                            return {"source": extractor.__name__.replace("extract_", "").replace("_data", ""), "path": path, "fields": fields}
            except Exception:
                continue
    return None


def parse_html(html: str, config: dict) -> ScrapedPostingPayload | None:
    """Extract job data from pre-fetched HTML."""
    source = config.get("source", "nextdata")
    if source == "nextdata":
        data = extract_next_data(html)
    elif source == "reactrouter":
        data = extract_react_router_data(html)
    elif source == "rsc":
        data = extract_rsc_data(html)
    elif source == "phenom_canvas":
        data = extract_phenom_canvas_data(html)
    else:
        data = None

    if data is None:
        return None

    path = config.get("path")
    item = resolve_path(data, path) if path else data
    if item is None:
        return None

    fields_map = config.get("fields", {})
    raw: dict[str, Any] = {}
    for target, spec in fields_map.items():
        val = extract_field(item, spec, root=data if isinstance(data, dict) else None)
        if val is not None:
            raw[target] = val

    return _map_to_payload(raw)


async def scrape(url: str, config: dict, http: httpx.AsyncClient) -> ScrapedPostingPayload | None:
    try:
        resp = await http.get(url, follow_redirects=True)
        resp.raise_for_status()
        return parse_html(resp.text, config)
    except Exception:
        return None


register_scraper("embedded", scrape, can_handle=can_handle)
register_scraper("nextdata", scrape)  # Alias
