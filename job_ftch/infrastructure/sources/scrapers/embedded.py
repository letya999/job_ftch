"""Embedded data scraper ported from jobseek."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from job_ftch.application.registry import register_scraper
from job_ftch.domain.site_models import ScrapedPostingPayload
from job_ftch.infrastructure.sources.embedded_state_utils import (
    extract_field,
    extract_inertia_data,
    extract_next_data,
    extract_nuxt_data,
    extract_phenom_canvas_data,
    extract_pinia_state,
    extract_react_router_data,
    extract_rsc_data,
    resolve_path,
)

if TYPE_CHECKING:
    import httpx

logger = structlog.get_logger("job_ftch.scrapers.embedded")

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


_JOB_KEYS = {
    "company",
    "salary",
    "location",
    "locations",
    "slug",
    "publishedAt",
    "postedAt",
    "datePosted",
    "employmentType",
    "jobType",
}


def _find_job_object(data: Any, path: str) -> tuple[str | None, dict[str, Any] | None]:
    """Recursively search for an object that looks like a job posting.

    Prioritizes arrays of job-like objects (e.g. Pinia featuredJobs.jobs)
    over single metadata objects (e.g. SEO blocks with title+description
    but no company/salary).
    """
    if isinstance(data, list):
        # If this is a list of dicts that look like jobs, return the first one
        if data and isinstance(data[0], dict):
            first_keys = set(data[0].keys())
            if _JOB_KEYS.intersection(first_keys):
                return f"{path}[0]" if path else "[0]", data[0]
        for index, value in enumerate(data):
            new_path = f"{path}[{index}]" if path else f"[{index}]"
            found_path, found_obj = _find_job_object(value, new_path)
            if found_obj:
                return found_path, found_obj
        return None, None

    if not isinstance(data, dict):
        return None, None

    # Check if this object itself is a job (requires title+desc AND job-specific keys)
    keys = set(data.keys())
    if (
        _TITLE_KEYS.intersection(keys)
        and _DESC_KEYS.intersection(keys)
        and _JOB_KEYS.intersection(keys)
    ):
        return path, data

    # Recurse into children, prioritizing keys that look job-related
    for key in sorted(
        data.keys(), key=lambda k: 0 if k in ("jobs", "jobList", "listings", "featuredJobs") else 1
    ):
        val = data[key]
        if isinstance(val, dict) or (isinstance(val, list) and val and isinstance(val[0], dict)):
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
    elif "locationSlugs" in keys:
        mapping["locations"] = "locationSlugs"

    # Map company (nested dict with name, or plain string)
    if "company" in keys:
        mapping["metadata.company"] = "company"
    if "salary" in keys:
        mapping["metadata.salary"] = "salary"
    if "skills" in keys:
        mapping["metadata.skills"] = "skills"
    if "publishedAt" in keys:
        mapping["metadata.publishedAt"] = "publishedAt"

    return mapping


def can_handle(htmls: list[str]) -> dict[str, Any] | None:
    """Detect embedded JSON with job objects across multiple pages."""
    for html in htmls:
        # Try different sources
        for source_name, extractor in [
            ("nextdata", extract_next_data),
            ("nuxt", extract_nuxt_data),
            ("inertia", extract_inertia_data),
            ("reactrouter", extract_react_router_data),
            ("phenom_canvas", extract_phenom_canvas_data),
            ("rsc", extract_rsc_data),
            ("pinia", extract_pinia_state),
        ]:
            try:
                data = extractor(html)
                if data:
                    path, obj = _find_job_object(data, "")
                    if obj:
                        fields = _auto_map_fields(obj)
                        if fields:
                            return {"source": source_name, "path": path, "fields": fields}
            except Exception as exc:
                logger.debug(
                    "embedded.can_handle.extractor_error", source=source_name, error=str(exc)
                )
                continue
    return None


def _extract_source_data(html: str, source: str) -> dict[str, Any] | None:
    if source == "nextdata":
        return extract_next_data(html)
    if source == "nuxt":
        return extract_nuxt_data(html)
    if source == "inertia":
        return extract_inertia_data(html)
    if source == "reactrouter":
        return extract_react_router_data(html)
    if source == "rsc":
        return extract_rsc_data(html)
    if source == "phenom_canvas":
        return extract_phenom_canvas_data(html)
    if source == "pinia":
        return extract_pinia_state(html)
    return None


def parse_html(html: str, config: dict[str, Any]) -> ScrapedPostingPayload | None:
    """Extract job data from pre-fetched HTML."""
    source = config.get("source")
    sources = (
        [source]
        if source
        else [
            "nextdata",
            "nuxt",
            "inertia",
            "reactrouter",
            "rsc",
            "phenom_canvas",
            "pinia",
        ]
    )

    for source_name in sources:
        data = _extract_source_data(html, source_name)
        if data is None:
            continue

        path = config.get("path")
        item = resolve_path(data, path) if path else data
        if item is None:
            continue

        fields_map = config.get("fields")
        if not fields_map:
            auto_path, auto_obj = _find_job_object(item, path or "")
            if auto_obj:
                fields_map = _auto_map_fields(auto_obj)
                item = auto_obj
                if auto_path and not path:
                    path = auto_path
        if not fields_map:
            continue

        raw: dict[str, Any] = {}
        for target, spec in fields_map.items():
            val = extract_field(item, spec, root=data if isinstance(data, dict) else None)
            if val is not None:
                raw[target] = val
        if raw:
            return _map_to_payload(raw)

    return None


async def scrape(
    url: str, config: dict[str, Any], http: httpx.AsyncClient
) -> ScrapedPostingPayload | None:
    try:
        html = config.get("prefetched_html")
        if not isinstance(html, str):
            resp = await http.get(url, follow_redirects=True)
            resp.raise_for_status()
            html = resp.text
        return parse_html(html, config)
    except Exception as exc:
        logger.warning("embedded.scrape.failed", url=url, error=str(exc))
        return None


register_scraper("embedded", scrape, can_handle=can_handle)
register_scraper("nextdata", scrape)  # Alias
