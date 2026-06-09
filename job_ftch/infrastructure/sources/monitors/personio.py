"""Personio monitor ported from jobseek."""

from __future__ import annotations

import logging
import re
import defusedxml.ElementTree as ET
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from job_ftch.application.registry import register_monitor
from job_ftch.infrastructure.sources.monitors.shared import (
    MAX_JOBS,
    fetch_page_text,
    truncated_rich_result,
)
from job_ftch.infrastructure.sources.site_models import (
    DiscoveredPostingPayload,
    MonitorResult,
)

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger("job_ftch.monitors.personio")

_DOMAIN_RE = re.compile(r"^([\w-]+)\.jobs\.personio\.(\w+)$")
_IGNORE_SLUGS = frozenset({"www", "api", "app", "docs", "help", "support", "status"})


def _slug_from_url(board_url: str) -> str | None:
    parsed = urlparse(board_url)
    host = (parsed.hostname or "").lower()
    match = _DOMAIN_RE.match(host)
    if match:
        slug = match.group(1)
        if slug not in _IGNORE_SLUGS:
            return slug
    return None


def _tld_from_url(board_url: str) -> str:
    parsed = urlparse(board_url)
    host = (parsed.hostname or "").lower()
    match = _DOMAIN_RE.match(host)
    if match:
        return match.group(2)
    return "de"


def _api_url(slug: str, domain: str = "de", lang: str = "en") -> str:
    return f"https://{slug}.jobs.personio.{domain}/xml?language={lang}"


def _board_base(slug: str, domain: str = "de") -> str:
    return f"https://{slug}.jobs.personio.{domain}"


def _text(el: ET.Element, tag: str) -> str | None:
    child = el.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return None


def _parse_description(position: ET.Element) -> str | None:
    descs_el = position.find("jobDescriptions")
    if descs_el is None:
        return None

    parts: list[str] = []
    for desc in descs_el.findall("jobDescription"):
        name = _text(desc, "name")
        value = _text(desc, "value")
        if value:
            if name:
                parts.append(f"<h3>{name}</h3>")
            parts.append(value)

    return "\n".join(parts) if parts else None


def _parse_job(position: ET.Element, slug: str, domain: str = "de") -> DiscoveredPostingPayload | None:
    pos_id = _text(position, "id")
    title = _text(position, "name")
    if not pos_id:
        return None

    url = f"{_board_base(slug, domain)}/job/{pos_id}"

    office = _text(position, "office")
    locations = [office] if office else None

    return DiscoveredPostingPayload(
        url=url,
        title=title,
        description=_parse_description(position),
        locations=locations,
        employment_type=_text(position, "employmentType"),
        date_posted=_text(position, "createdAt"),
    )


async def discover(spec: Any, client: httpx.AsyncClient, auth: Any = None) -> MonitorResult | list[DiscoveredPostingPayload]:
    board_url = spec.url
    metadata = spec.monitor_config
    slug = metadata.get("slug") or _slug_from_url(board_url)
    if not slug:
        raise ValueError(f"Cannot derive Personio slug from {board_url!r}")

    domain = metadata.get("domain") or _tld_from_url(board_url)
    lang = metadata.get("language", "en")

    url = _api_url(slug, domain, lang)
    try:
        resp = await client.get(url, follow_redirects=True)
        if resp.status_code != 200:
            return []
        root = ET.fromstring(resp.text)
    except Exception:
        return []

    positions = root.findall(".//position")
    jobs: list[DiscoveredPostingPayload] = []
    for pos in positions:
        parsed = _parse_job(pos, slug, domain)
        if parsed:
            jobs.append(parsed)

    if len(jobs) > MAX_JOBS:
        return truncated_rich_result(jobs)
    return jobs


async def can_handle(url: str, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
    slug = _slug_from_url(url)
    if slug:
        return {"slug": slug, "domain": _tld_from_url(url)}

    if client is None:
        return None

    html = await fetch_page_text(url, client)
    if html and (".jobs.personio." in html or "personio." in html):
        match = _DOMAIN_RE.search(html)
        if match:
            return {"slug": match.group(1), "domain": match.group(2)}

    return None


register_monitor("personio", discover, cost=10, rich=True, can_handle=can_handle)
