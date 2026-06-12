"""Generic API-sniffer monitor for SPA career sites.

Captures JSON/XHR responses in a browser session and extracts either:
- rich payloads when the API returns full job objects;
- URL-only results when the API returns links to detail pages.
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

import structlog

from job_ftch.application.registry import register_monitor
from job_ftch.domain.site_models import DiscoveredPostingPayload, MonitorResult

if TYPE_CHECKING:
    import httpx

log = structlog.get_logger("job_ftch.monitors.api_sniffer")

_API_HINT_RE = re.compile(r"(fetch\(|axios|graphql|/api/|vacanc|job[s_/:-])", re.IGNORECASE)
_ABS_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_PATH_HINT_RE = re.compile(
    r"(/[a-z0-9\-_/]*(?:job|jobs|vacancy|vacancies)[a-z0-9\-_/]*)",
    re.IGNORECASE,
)
_TITLE_KEYS = ("title", "name", "jobTitle", "job_title", "position")
_DESC_KEYS = (
    "description",
    "content",
    "descriptionHtml",
    "body",
    "jobDescription",
    "about",
    "short_description",
    "full_description",
)


def _iter_nodes(node: Any) -> Any:
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from _iter_nodes(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_nodes(value)


def _pick_first(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value:
            return value
    return None


def _normalize_url(value: str, base_url: str) -> str | None:
    candidate = value.strip()
    if not candidate:
        return None
    if candidate.startswith("http://") or candidate.startswith("https://"):
        return candidate
    if candidate.startswith("/"):
        return urljoin(base_url, candidate)
    return None


def _looks_like_job_dict(node: dict[str, Any]) -> bool:
    has_title = any(node.get(key) for key in _TITLE_KEYS)
    if not has_title:
        return False
    has_url = any(
        node.get(key)
        for key in (
            "url",
            "jobUrl",
            "job_url",
            "absolute_url",
            "alternate_url",
            "viewUrl",
            "applyUrl",
        )
    )
    has_desc = any(node.get(key) for key in _DESC_KEYS)
    return bool(has_url or has_desc)


def _payload_from_dict(node: dict[str, Any], base_url: str) -> DiscoveredPostingPayload | None:
    raw_url = _pick_first(
        node,
        ("url", "jobUrl", "job_url", "absolute_url", "alternate_url", "viewUrl", "applyUrl"),
    )
    if isinstance(raw_url, str):
        url = _normalize_url(raw_url, base_url)
        if url is None:
            return None
    else:
        job_id = _pick_first(node, ("vacancy_id", "vacancyId", "id", "jobId", "job_id"))
        title = _pick_first(node, _TITLE_KEYS)
        if not (job_id and title):
            return None
        url = f"{base_url.rstrip('/')}/#job-{job_id}"

    title = _pick_first(node, _TITLE_KEYS)
    description = _pick_first(node, _DESC_KEYS)
    locations = _pick_first(node, ("locations", "location", "cityName", "city", "area"))
    if isinstance(locations, str):
        locations = [locations]
    elif isinstance(locations, dict):
        locations = [str(v) for v in locations.values() if v]
    elif isinstance(locations, list):
        locations = [str(v) for v in locations if v]
    else:
        locations = None

    metadata: dict[str, Any] = {}
    for key in (
        "id",
        "companyName",
        "company",
        "department",
        "team",
        "employment",
        "schedule",
        "source",
    ):
        value = node.get(key)
        if value:
            metadata[key] = value

    return DiscoveredPostingPayload(
        url=url,
        title=str(title) if title else None,
        description=str(description) if description else None,
        locations=locations or None,
        metadata=metadata or None,
    )


def _collect_urls(node: Any, base_url: str) -> set[str]:
    urls: set[str] = set()
    for value in _iter_nodes(node):
        if isinstance(value, str):
            for match in _ABS_URL_RE.findall(value):
                if any(token in match.lower() for token in ("job", "jobs", "vacanc")):
                    urls.add(match)
            for match in _PATH_HINT_RE.findall(value):
                normalized = _normalize_url(match, base_url)
                if normalized:
                    urls.add(normalized)
        elif isinstance(value, dict):
            payload = _payload_from_dict(value, base_url)
            if payload:
                urls.add(payload.url)
    return urls


def _collect_payloads(node: Any, base_url: str) -> dict[str, DiscoveredPostingPayload]:
    payloads: dict[str, DiscoveredPostingPayload] = {}
    for value in _iter_nodes(node):
        if isinstance(value, dict) and _looks_like_job_dict(value):
            payload = _payload_from_dict(value, base_url)
            if payload:
                payloads[payload.url] = payload
    return payloads


async def can_handle(url: str, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
    if client is None:
        return None
    try:
        resp = await client.get(url, follow_redirects=True)
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    if _API_HINT_RE.search(resp.text):
        return {"browser": True}
    return None


async def discover(spec: Any, client: httpx.AsyncClient, auth: Any = None) -> MonitorResult:
    del client, auth
    try:
        from playwright.async_api import async_playwright

        from job_ftch.infrastructure.sources.browser_utils import (
            BROWSER_KEYS,
            open_page,
            run_actions,
        )
    except ImportError as exc:
        raise RuntimeError(
            "playwright is required for api_sniffer monitor. "
            "Install: uv add playwright --optional browser && playwright install chromium"
        ) from exc

    config = dict(spec.monitor_config or {})
    board_url = spec.url
    browser_config = {k: v for k, v in config.items() if k in BROWSER_KEYS}
    response_payloads: list[tuple[str, Any]] = []
    settle_seconds = float(config.get("settle_seconds", 4))
    api_pattern = config.get("api_url_match")

    async def capture_response(response: Any) -> None:
        try:
            resp_url = response.url
            if api_pattern and not re.search(api_pattern, resp_url, re.IGNORECASE):
                return

            headers = await response.all_headers()
            content_type = headers.get("content-type", "")
            if "json" not in content_type.lower() and not _API_HINT_RE.search(resp_url):
                return

            # Read immediately while resource is hot
            data = await response.json()
            response_payloads.append((resp_url, data))
        except Exception:
            return  # resource evicted, non-JSON, redirect — skip silently

    async with async_playwright() as pw, open_page(pw, browser_config) as page:
        # Register as async handler directly
        page.on("response", lambda r: asyncio.ensure_future(capture_response(r)))

        await page.goto(board_url, wait_until=config.get("wait", "domcontentloaded"))
        actions = config.get("actions")
        if actions:
            await run_actions(page, actions)

        # Replace fixed sleep with networkidle
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            await asyncio.sleep(settle_seconds)

    payloads: dict[str, DiscoveredPostingPayload] = {}
    urls: set[str] = set()

    for _, data in response_payloads:
        payloads.update(_collect_payloads(data, board_url))
        urls.update(_collect_urls(data, board_url))

    if payloads:
        payload_urls = set(payloads.keys())
        return MonitorResult(urls=payload_urls, payloads_by_url=payloads)
    return MonitorResult(urls=urls)


register_monitor("api_sniffer", discover, cost=200, rich=False, can_handle=can_handle)
