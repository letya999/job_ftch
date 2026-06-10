"""Next.js __NEXT_DATA__ / RSC scraper — pre-configured embedded scraper."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_scraper
from job_ftch.infrastructure.sources.nextdata_utils import (
    extract_next_data,
    extract_rsc_data,
    resolve_path,
)
from job_ftch.infrastructure.sources.scrapers.embedded import (
    _auto_map_fields,
    _find_job_object,
)
from job_ftch.infrastructure.sources.scrapers.embedded import parse_html as embedded_parse_html
from job_ftch.infrastructure.sources.scrapers.embedded import scrape as embedded_scrape

if TYPE_CHECKING:
    import httpx

    from job_ftch.infrastructure.sources.site_models import ScrapedPostingPayload


def _inject_script_id(config: dict[str, Any]) -> dict[str, Any]:
    if config.get("source"):
        return config
    merged = dict(config)
    merged["script_id"] = "__NEXT_DATA__"
    return merged


def _try_rsc_detection(htmls: list[str]) -> dict[str, Any] | None:
    job_objects: list[tuple[str | None, dict[str, Any]]] = []
    for html in htmls:
        data = extract_rsc_data(html)
        if not isinstance(data, dict):
            continue
        suffix, job_obj = _find_job_object(data, "")
        if job_obj:
            job_objects.append((suffix, job_obj))

    if not job_objects:
        return None

    from collections import Counter

    suffix_counts = Counter(suffix for suffix, _ in job_objects)
    best_suffix = suffix_counts.most_common(1)[0][0]
    matching_objs = [obj for suffix, obj in job_objects if suffix == best_suffix]

    all_keys: set[str] = set()
    for obj in matching_objs:
        all_keys.update(obj.keys())
    merged: dict[str, Any] = {}
    for key in all_keys:
        for obj in matching_objs:
            if key in obj:
                merged[key] = obj[key]
                break

    fields = _auto_map_fields(merged)
    if not fields:
        return None

    config: dict[str, Any] = {"source": "rsc", "fields": fields}
    if best_suffix:
        config["path"] = best_suffix
    return config


def can_handle(htmls: list[str]) -> dict[str, Any] | None:
    job_objects: list[tuple[str | None, dict[str, Any]]] = []

    for html in htmls:
        data = extract_next_data(html)
        if data is None:
            continue
        page_props = resolve_path(data, "props.pageProps")
        if not isinstance(page_props, dict):
            continue
        suffix, job_obj = _find_job_object(page_props, "props.pageProps")
        if job_obj:
            job_objects.append((suffix, job_obj))

    if not job_objects:
        rsc_result = _try_rsc_detection(htmls)
        if rsc_result:
            return rsc_result
        return None

    from collections import Counter

    suffix_counts = Counter(suffix for suffix, _ in job_objects)
    best_suffix = suffix_counts.most_common(1)[0][0]

    matching_objs = [obj for suffix, obj in job_objects if suffix == best_suffix]

    all_keys: set[str] = set()
    for obj in matching_objs:
        all_keys.update(obj.keys())

    merged: dict[str, Any] = {}
    for key in all_keys:
        for obj in matching_objs:
            if key in obj:
                merged[key] = obj[key]
                break

    fields = _auto_map_fields(merged)
    if not fields:
        return None

    config: dict[str, Any] = {"fields": fields}
    if best_suffix:
        config["path"] = f"props.pageProps.{best_suffix}"
    else:
        config["path"] = "props.pageProps"
    return config


def parse_html(html: str, config: dict[str, Any]) -> ScrapedPostingPayload | None:
    return embedded_parse_html(html, _inject_script_id(config))


async def scrape(
    url: str, config: dict[str, Any], http: httpx.AsyncClient
) -> ScrapedPostingPayload | None:
    return await embedded_scrape(url, _inject_script_id(config), http)


register_scraper("nextdata", scrape, can_handle=can_handle)
