"""Embedded JSON monitor (Next.js, etc.) ported from jobseek."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from job_ftch.application.registry import register_monitor
from job_ftch.domain.site_models import (
    DiscoveredPostingPayload,
    MonitorResult,
)
from job_ftch.infrastructure.sources.embedded_state_utils import (
    extract_embedded_json,
    extract_field,
    extract_next_data,
    extract_phenom_canvas_data,
    extract_react_router_data,
    extract_rsc_data,
    resolve_path,
    slugify,
)
from job_ftch.infrastructure.sources.monitors.shared import (
    fetch_page_text,
)

if TYPE_CHECKING:
    import httpx

logger = structlog.get_logger("job_ftch.monitors.nextdata")

_COMMON_PATHS = [
    "props.pageProps.positions",
    "props.pageProps.jobs",
    "props.pageProps.openings",
    "props.pageProps.allJobs",
    "props.pageProps.data.positions",
    "props.pageProps.data.jobs",
    "props.pageProps.initialState.jobs.items",
]


def _build_url(
    item: dict[str, Any],
    url_template: str | None,
    slug_fields: list[str] | None,
    board_url: str = "",
) -> str | None:
    if url_template is None:
        # Try heuristic fallback
        for key in ["url", "link", "href"]:
            if key in item and isinstance(item[key], str) and item[key].startswith(("http", "/")):
                val = item[key]
                if val.startswith("/"):
                    from urllib.parse import urlparse

                    parsed = urlparse(board_url)
                    return f"{parsed.scheme}://{parsed.netloc}{val}"
                return str(val)
        # Several SSR career sites keep the canonical vacancy locator in an
        # SEO object while the top-level record only has an internal id.
        # Reuse it before manufacturing a ``/job/{id}`` URL: the latter does
        # not exist on platforms such as EPAM Careers.
        seo = item.get("seo")
        if isinstance(seo, dict) and isinstance(seo.get("url"), str):
            val = str(seo["url"])
            if val.startswith("/"):
                from urllib.parse import urlparse

                parsed = urlparse(board_url)
                return f"{parsed.scheme}://{parsed.netloc}{val}"
            if val.startswith("http"):
                return val
        return None

    variables: dict[str, Any] = {}
    for key, value in item.items():
        if isinstance(value, (str, int, float)):
            variables[key] = value

    if slug_fields:
        parts: list[str] = []
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


def _find_jobs_path(data: dict[str, Any], paths: list[str] | None = None) -> tuple[str, int] | None:
    for path in paths or _COMMON_PATHS:
        arr = resolve_path(data, path)
        if (
            isinstance(arr, list)
            and len(arr) >= 1
            and all(isinstance(item, dict) for item in arr[:1])
        ):
            return path, len(arr)

    # Heuristic fallback: find array of dicts with job-like keys
    best_path = None
    best_score = -1
    best_len = 0

    def walk(obj: Any, current_path: str) -> None:
        nonlocal best_path, best_score, best_len
        if isinstance(obj, dict):
            for k, v in obj.items():
                escaped_k = f'"{k}"' if not str(k).isalnum() else str(k)
                next_path = f"{current_path}.{escaped_k}" if current_path else escaped_k
                walk(v, next_path)
        elif isinstance(obj, list):
            if len(obj) > 0 and isinstance(obj[0], dict):
                sample = obj[0]
                keys = {str(k).lower() for k in sample}
                score = 0

                # Score title/id
                if any(k in keys for k in ["title", "name", "role", "position"]):
                    score += 1
                if any(k in keys for k in ["id", "slug", "url", "urlstring", "link", "reqid"]):
                    score += 1

                # Score context
                if any("location" in k or "country" in k or "city" in k for k in keys):
                    score += 1
                if any("salary" in k or "compensation" in k or "rate" in k for k in keys):
                    score += 1
                if any("description" in k or "summary" in k or "content" in k for k in keys):
                    score += 1
                if any(
                    "job" in k or "company" in k or "vacancy" in k or "posting" in k for k in keys
                ):
                    score += 1
                # These fields describe a concrete, publishable vacancy and
                # are deliberately weighted above generic CMS content that
                # happens to mention jobs (forms, disclaimers, page copy).
                if any(
                    key in keys
                    for key in (
                        "unique_id",
                        "requisition_id",
                        "is_expired",
                        "application_type",
                        "posting_type",
                        "vacancy_type",
                    )
                ):
                    score += 6

                # Content-management records use title/uid/description too,
                # but they are page chrome rather than vacancies.  Prefer a
                # record with actual job lifecycle fields over CMS entries.
                if any(
                    key in keys
                    for key in (
                        "_content_type_uid",
                        "locale",
                        "updated_at",
                        "publish_details",
                        "acl",
                        "_version",
                    )
                ):
                    score -= 3

                if score >= 2 and (
                    score > best_score or (score == best_score and len(obj) > best_len)
                ):
                    best_score = score
                    best_path = current_path
                    best_len = len(obj)

                # Keep digging a bit (e.g. pagination edges array)
                if score < 3:
                    for i in range(min(3, len(obj))):
                        walk(obj[i], f"{current_path}[{i}]")
            else:
                for i, item in enumerate(obj):
                    if isinstance(item, (dict, list)):
                        walk(item, f"{current_path}[{i}]")

    walk(data, "")

    if best_path:
        return best_path, best_len

    return None


def _heuristic_extract_job(item: dict[str, Any], url: str) -> DiscoveredPostingPayload:
    payload = DiscoveredPostingPayload(url=url)

    keys_map = {str(k).lower(): k for k in item}

    title_keys = ["title", "name", "role", "position"]
    for k in title_keys:
        if k in keys_map and isinstance(item[keys_map[k]], str):
            payload.title = item[keys_map[k]]
            break

    location_keys = ["location", "locations", "city", "country"]
    for k in location_keys:
        if k in keys_map:
            val = item[keys_map[k]]
            if isinstance(val, str):
                payload.locations = [val]
                break
            elif isinstance(val, list) and len(val) > 0 and isinstance(val[0], str):
                payload.locations = [val[0]]
                break
            elif isinstance(val, dict) and "name" in val:
                payload.locations = [str(val["name"])]
                break

    salary_keys = ["salary", "compensation"]
    for k in salary_keys:
        if k in keys_map:
            payload.extras = {"salary_raw": str(item[keys_map[k]])}
            break

    desc_keys = ["description", "summary", "content"]
    for k in desc_keys:
        if k in keys_map and isinstance(item[keys_map[k]], str):
            payload.description = item[keys_map[k]]
            break

    return payload


async def discover(
    spec: Any, client: httpx.AsyncClient, auth: Any = None
) -> MonitorResult | set[str] | list[DiscoveredPostingPayload]:
    board_url = spec.url
    metadata = spec.monitor_config

    path = metadata.get("path")
    url_template = metadata.get("url_template")
    source = metadata.get("source", "nextdata")
    fields_map = metadata.get("fields")

    from job_ftch.config import get_settings

    html = await fetch_page_text(
        board_url,
        client,
        max_chars=get_settings().embedded_state_page_text_max_chars,
    )
    if not html:
        return set()

    data = extract_embedded_json(html, source)
    if not data:
        return set()

    if not path:
        # Try heuristic if not provided
        found = _find_jobs_path(data)
        if found:
            path, _ = found

    if not path:
        return set()

    items = resolve_path(data, path)
    if not isinstance(items, list):
        return set()

    if fields_map:
        # Rich mode with explicit fields
        field_jobs: list[DiscoveredPostingPayload] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            url = _build_url(item, url_template, metadata.get("slug_fields"), board_url=board_url)
            if not url:
                continue

            job_kwargs: dict[str, Any] = {"url": url}
            for target, field_spec in fields_map.items():
                val = extract_field(item, field_spec, root=data)
                if val is not None:
                    job_kwargs[target] = val

            field_jobs.append(DiscoveredPostingPayload(**job_kwargs))
        return field_jobs

    elif url_template is None:
        # Heuristic rich mode (no fields map, no URL template)
        heuristic_jobs: list[DiscoveredPostingPayload] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            url = _build_url(item, None, None, board_url=board_url)
            if not url:
                if "urlString" in item:
                    url = str(item["urlString"])
                    if url.startswith("/"):
                        url = f"{board_url.rstrip('/')}{url}"
                elif "url" in item:
                    url = str(item["url"])
                elif "id" in item:
                    url = f"{board_url.rstrip('/')}/job/{item['id']}"
                elif "slug" in item:
                    url = f"{board_url.rstrip('/')}/job/{item['slug']}"
                else:
                    continue
            heuristic_jobs.append(_heuristic_extract_job(item, url))
        return heuristic_jobs

    # URL-only mode
    urls = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        url = _build_url(item, url_template, metadata.get("slug_fields"), board_url=board_url)
        if url:
            urls.add(url)
    return urls


async def can_handle(url: str, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
    if client is None:
        return None
    from job_ftch.config import get_settings

    html = await fetch_page_text(
        url,
        client,
        max_chars=get_settings().embedded_state_page_text_max_chars,
    )
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


register_monitor(
    "nextdata",
    discover,
    cost=30,
    rich=False,
    can_handle=can_handle,
    scraper_chain=("nextdata", "json-ld", "embedded", "maintext"),
)
