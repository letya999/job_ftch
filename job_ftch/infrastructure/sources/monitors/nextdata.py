"""Embedded JSON monitor (Next.js, etc.) ported from jobseek."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_monitor
from job_ftch.infrastructure.sources.monitors.shared import (
    fetch_page_text,
)
from job_ftch.infrastructure.sources.nextdata_utils import (
    extract_embedded_json,
    extract_field,
    extract_next_data,
    extract_phenom_canvas_data,
    extract_react_router_data,
    extract_rsc_data,
    resolve_path,
    slugify,
)
from job_ftch.infrastructure.sources.site_models import (
    DiscoveredPostingPayload,
    MonitorResult,
)

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger("job_ftch.monitors.nextdata")

_COMMON_PATHS = [
    "props.pageProps.positions",
    "props.pageProps.jobs",
    "props.pageProps.openings",
    "props.pageProps.allJobs",
    "props.pageProps.data.positions",
    "props.pageProps.data.jobs",
    "props.pageProps.initialState.jobs.items",
]


def _build_url(item: dict, url_template: str, slug_fields: list[str] | None) -> str | None:
    variables: dict[str, Any] = {}
    for key, value in item.items():
        if isinstance(value, (str, int, float)):
            variables[key] = value

    if slug_fields:
        parts = []
        for field in slug_fields:
            val = item.get(field)
            if val is not None:
                parts.append(slugify(str(val)))
        if parts:
            variables["slug"] = "-".join(parts)

    try:
        return url_template.format_map(variables)
    except (KeyError, IndexError, ValueError):
        return None


def _find_jobs_path(data: dict, paths: list[str] | None = None) -> tuple[str, int] | None:
    for path in paths or _COMMON_PATHS:
        arr = resolve_path(data, path)
        if (
            isinstance(arr, list)
            and len(arr) >= 1
            and all(isinstance(item, dict) for item in arr[:1])
        ):
            return path, len(arr)
    return None


async def discover(spec: Any, client: httpx.AsyncClient, auth: Any = None) -> MonitorResult | set[str] | list[DiscoveredPostingPayload]:
    board_url = spec.url
    metadata = spec.monitor_config

    path = metadata.get("path")
    url_template = metadata.get("url_template")
    source = metadata.get("source", "nextdata")
    fields_map = metadata.get("fields")

    html = await fetch_page_text(board_url, client)
    if not html:
        return set()

    data = extract_embedded_json(html, source)
    if not data:
        return set()

    items = resolve_path(data, path)
    if not isinstance(items, list):
        return set()

    if fields_map:
        # Rich mode
        jobs = []
        for item in items:
            if not isinstance(item, dict):
                continue
            url = _build_url(item, url_template, metadata.get("slug_fields"))
            if not url:
                continue
            
            job_kwargs: dict[str, Any] = {"url": url}
            for target, field_spec in fields_map.items():
                val = extract_field(item, field_spec, root=data)
                if val is not None:
                    job_kwargs[target] = val
            
            jobs.append(DiscoveredPostingPayload(**job_kwargs))
        return jobs

    # URL-only mode
    urls = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        url = _build_url(item, url_template, metadata.get("slug_fields"))
        if url:
            urls.add(url)
    return urls


async def can_handle(url: str, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
    if client is None:
        return None
    html = await fetch_page_text(url, client)
    if not html:
        return None

    for extractor, paths in [
        (extract_next_data, _COMMON_PATHS),
        (extract_react_router_data, None),
        (extract_rsc_data, None),
        (extract_phenom_canvas_data, None),
    ]:
        data = extractor(html)
        if data:
            result = _find_jobs_path(data, paths)
            if result:
                path, count = result
                return {"path": path, "count": count}
    return None


register_monitor("nextdata", discover, cost=30, rich=False, can_handle=can_handle)
