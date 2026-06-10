from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from job_ftch.domain.models import SourceKind
from job_ftch.domain.site_models import (
    DiscoveredPostingPayload,
    MonitorResult,
    ScrapedPostingPayload,
)
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item

if TYPE_CHECKING:
    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


def normalize_monitor_result(raw: Any) -> MonitorResult:
    """Normalize various return types from monitors into MonitorResult."""
    if isinstance(raw, MonitorResult):
        return raw
    if isinstance(raw, list):
        # List of payloads
        payloads = {p.url: p for p in raw if hasattr(p, "url")}
        return MonitorResult(urls=set(payloads.keys()), payloads_by_url=payloads)
    if isinstance(raw, set):
        return MonitorResult(urls=raw)
    if isinstance(raw, tuple) and len(raw) == 2 and isinstance(raw[0], set):
        # (URLs, discovered sitemap URL)
        return MonitorResult(
            urls=raw[0],
            metadata_updates={"discovered_url": raw[1]} if raw[1] else {},
        )
    return MonitorResult()


def apply_url_filter(
    result: MonitorResult, url_filter_config: str | dict[str, Any] | None
) -> MonitorResult:
    """Apply regex include/exclude filters to discovered URLs."""
    if not url_filter_config:
        return result

    include = None
    exclude = None
    if isinstance(url_filter_config, str):
        include = url_filter_config
    elif isinstance(url_filter_config, dict):
        include = url_filter_config.get("include")
        exclude = url_filter_config.get("exclude")

    filtered_urls: set[str] = set()
    filtered_payloads: dict[str, DiscoveredPostingPayload] = {}
    removed_count = 0

    for url in result.urls:
        keep = True
        if include and not re.search(include, url):
            keep = False
        if exclude and re.search(exclude, url):
            keep = False

        if keep:
            filtered_urls.add(url)
            if url in result.payloads_by_url:
                filtered_payloads[url] = result.payloads_by_url[url]
        else:
            removed_count += 1

    result.urls = filtered_urls
    result.payloads_by_url = filtered_payloads
    result.filtered_count += removed_count
    return result


def apply_url_transform(
    result: MonitorResult, url_transform_config: dict[str, Any] | None
) -> MonitorResult:
    """Apply regex find/replace transforms to discovered URLs."""
    if not url_transform_config:
        return result

    find = url_transform_config.get("find")
    replace = url_transform_config.get("replace", "")
    if not find:
        return result

    transformed_urls: set[str] = set()
    transformed_payloads: dict[str, DiscoveredPostingPayload] = {}

    for url in result.urls:
        new_url = re.sub(str(find), str(replace), url)
        transformed_urls.add(new_url)
        if url in result.payloads_by_url:
            payload = result.payloads_by_url[url]
            import dataclasses

            new_payload = dataclasses.replace(payload, url=new_url)
            transformed_payloads[new_url] = new_payload

    result.urls = transformed_urls
    result.payloads_by_url = transformed_payloads
    return result


def enrich_description(
    payload: DiscoveredPostingPayload | ScrapedPostingPayload,
) -> str | None:
    """Append extras (skills, responsibilities, etc.) to description HTML."""
    desc = payload.description or ""
    if not payload.extras:
        return payload.description or None

    extra_parts = []
    # Common keys ported from jobseek
    for key in (
        "responsibilities",
        "qualifications",
        "requirements",
        "skills",
        "benefits",
        "perks",
    ):
        if value := payload.extras.get(key):
            if isinstance(value, list):
                value = "<ul>" + "".join(f"<li>{v}</li>" for v in value) + "</ul>"
            extra_parts.append(f"<h3>{key.capitalize()}</h3>\n{value}")

    if extra_parts:
        if desc:
            desc += "\n<hr/>\n"
        desc += "\n\n".join(extra_parts)

    return desc or None


def payload_to_raw_item(
    payload: DiscoveredPostingPayload,
    spec: CareerSiteSpec,
    source_name: str,
) -> RawItem:
    """Convert infra-layer payload to domain-layer RawItem."""
    description = enrich_description(payload)
    title = payload.title or "Unknown Job"
    text = f"{title}\n\n{description}" if description else title

    meta: dict[str, Any] = {**(payload.metadata or {}), **(payload.localizations or {})}
    meta["title"] = title
    if payload.locations:
        meta["locations"] = payload.locations
    if payload.employment_type:
        meta["employment_type"] = payload.employment_type
    if payload.job_location_type:
        meta["job_location_type"] = payload.job_location_type
    if payload.date_posted:
        meta["date_posted"] = payload.date_posted
    if payload.base_salary:
        meta["base_salary"] = payload.base_salary
    if payload.language:
        meta["language"] = payload.language
    if payload.extras:
        meta["extras"] = payload.extras

    return build_raw_item(
        url=payload.url,
        text=text,
        source_name=source_name,
        source_kind=SourceKind.CAREER_SITE,
        external_id=None,
        metadata=meta,
    )
