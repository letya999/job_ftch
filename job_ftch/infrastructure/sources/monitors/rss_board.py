"""Generic RSS 2.0 feed monitor ported from jobseek."""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import defusedxml.ElementTree as ET

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

logger = logging.getLogger("job_ftch.monitors.rss_board")

_TT_NS = "https://teamtailor.com/locations"
_G_NS = "http://base.google.com/ns/1.0"
_TITLE_LOCATION_RE = re.compile(r"\s*\([^)]+,\s*[^)]+\)\s*$")


@dataclass(frozen=True)
class _Preset:
    feed_paths: list[str]
    page_patterns: list[re.Pattern]
    feed_ns: dict[str, str]
    paginated: bool = False
    page_size: int = 100


_PRESETS: dict[str, _Preset] = {
    "successfactors": _Preset(
        feed_paths=["/googlefeed.xml"],
        page_patterns=[
            re.compile(r"successfactors\.(?:eu|com)"),
            re.compile(r"rmkcdn\.successfactors\.com"),
            re.compile(r"jobs2web\.com"),
        ],
        feed_ns={"g": _G_NS},
    ),
    "teamtailor": _Preset(
        feed_paths=["/jobs.rss"],
        page_patterns=[
            re.compile(r"teamtailor-cdn\.com"),
        ],
        feed_ns={"tt": _TT_NS},
        paginated=True,
        page_size=100,
    ),
}


def _text(item: ET.Element, tag: str) -> str | None:
    child = item.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return None


def _g(item: ET.Element, tag: str) -> str | None:
    child = item.find(f"{{{_G_NS}}}{tag}")
    if child is not None and child.text:
        return child.text.strip()
    return None


def _tt(item: ET.Element, tag: str) -> str | None:
    child = item.find(f"{{{_TT_NS}}}{tag}")
    if child is not None and child.text:
        return child.text.strip()
    return None


def _parse_sf_item(item: ET.Element) -> DiscoveredPostingPayload | None:
    link = _text(item, "link")
    title = _text(item, "title")
    if not link:
        return None

    raw_desc = _text(item, "description")
    description = html.unescape(raw_desc) if raw_desc else None

    location = _g(item, "location")
    locations = [location] if location else None

    if title and location:
        cleaned = _TITLE_LOCATION_RE.sub("", title)
        if cleaned:
            title = cleaned

    metadata: dict = {}
    if job_id := _text(item, "guid"):
        metadata["id"] = job_id
    if employer := _g(item, "employer"):
        metadata["employer"] = employer

    return DiscoveredPostingPayload(
        url=link,
        title=title,
        description=description,
        locations=locations,
        metadata=metadata or None,
    )


def _parse_tt_item(item: ET.Element) -> DiscoveredPostingPayload | None:
    link = _text(item, "link")
    title = _text(item, "title")
    if not link:
        return None

    raw_desc = _text(item, "description")
    description = html.unescape(raw_desc) if raw_desc else None

    locations: list[str] = []
    locations_el = item.find(f"{{{_TT_NS}}}locations")
    if locations_el is not None:
        for loc_el in locations_el.findall(f"{{{_TT_NS}}}location"):
            name_el = loc_el.find(f"{{{_TT_NS}}}name")
            if name_el is not None and name_el.text:
                locations.append(name_el.text.strip())

    remote_status = _text(item, "remoteStatus")
    job_location_type: str | None = None
    if remote_status:
        lower = remote_status.lower()
        if "fully" in lower or lower == "remote":
            job_location_type = "remote"
        elif "hybrid" in lower:
            job_location_type = "hybrid"

    return DiscoveredPostingPayload(
        url=link,
        title=title,
        description=description,
        locations=locations or None,
        job_location_type=job_location_type,
        date_posted=_text(item, "pubDate"),
    )


def _parse_generic_item(item: ET.Element) -> DiscoveredPostingPayload | None:
    link = _text(item, "link")
    title = _text(item, "title")
    if not link:
        return None

    raw_desc = _text(item, "description")
    description = html.unescape(raw_desc) if raw_desc else None

    return DiscoveredPostingPayload(
        url=link,
        title=title,
        description=description,
        date_posted=_text(item, "pubDate"),
    )


async def discover(
    spec: Any, client: httpx.AsyncClient, auth: Any = None
) -> MonitorResult | list[DiscoveredPostingPayload]:
    board_url = spec.url
    metadata = spec.monitor_config
    preset_name = metadata.get("preset", "generic")
    feed_url = metadata.get("feed_url")

    if not feed_url:
        preset = _PRESETS.get(preset_name)
        if preset:
            parsed = urlparse(board_url)
            feed_url = f"{parsed.scheme}://{parsed.netloc}{preset.feed_paths[0]}"

    if not feed_url:
        return []

    resp = await client.get(feed_url, follow_redirects=True)
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    channel = root.find("channel")
    items = channel.findall("item") if channel is not None else []

    parser = {
        "successfactors": _parse_sf_item,
        "teamtailor": _parse_tt_item,
    }.get(preset_name, _parse_generic_item)

    jobs: list[DiscoveredPostingPayload] = []
    for item in items:
        parsed = parser(item)
        if parsed:
            jobs.append(parsed)

    if len(jobs) > MAX_JOBS:
        return truncated_rich_result(jobs)

    return jobs


async def can_handle(url: str, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
    if client is None:
        return None

    html_text = await fetch_page_text(url, client)
    if not html_text:
        return None

    for preset_name, preset in _PRESETS.items():
        if any(p.search(html_text) for p in preset.page_patterns):
            parsed = urlparse(url)
            feed = f"{parsed.scheme}://{parsed.netloc}{preset.feed_paths[0]}"
            return {"preset": preset_name, "feed_url": feed}

    return None


register_monitor("rss_board", discover, cost=40, rich=True, can_handle=can_handle)
