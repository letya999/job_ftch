"""Inline (config-driven step DSL) job URL discovery monitor.

Lets YAML-only config express common fetch patterns without writing Python code.
Each step in the ``steps`` list is executed in order; URLs discovered across all
steps are unioned into the final result.

Supported step types:

  ``fetch_json``
    GET a JSON endpoint; extract URLs from a nested field.

    Required keys:
      ``url``          – endpoint to fetch (defaults to ``spec.url`` if omitted)

    Optional keys:
      ``items_path``   – dot-separated key path to the list of job objects,
                         e.g. ``"data.jobs"`` navigates ``resp["data"]["jobs"]``.
                         Omit if the top-level response is already a list.
      ``url_field``    – field within each list item that holds the posting URL
                         (default: ``"url"``)
      ``params``       – dict of query-string parameters appended to the request

  ``fetch_html``
    GET an HTML page; extract ``<a href>`` links matching an optional filter.

    Required keys:
      ``url``          – page to fetch (defaults to ``spec.url`` if omitted)

    Optional keys:
      ``url_filter``   – regex applied to discovered href values; only matching
                         URLs are kept.  Omit to use the default job-URL heuristics.

Cost: 60 — between dom (50) and api_sniffer (200).  Makes targeted HTTP requests
to known endpoints so it is cheaper than full DOM scraping but more expensive than
a pure API call (which already has its own dedicated monitor).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import structlog

from job_ftch.application.registry import register_monitor

if TYPE_CHECKING:
    import httpx

log = structlog.get_logger("job_ftch.monitors.inline")

_MAX_URLS = 5_000


def _nested_get(obj: Any, path: str) -> Any:
    """Traverse a dot-separated key path into nested dicts."""
    current = obj
    for key in path.split("."):
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return current
    return current


def _extract_urls_from_json(
    data: Any,
    items_path: str | None,
    url_field: str,
) -> set[str]:
    """Extract URLs from a JSON response using a path + field name."""
    if items_path:
        items = _nested_get(data, items_path)
    elif isinstance(data, list):
        items = data
    else:
        items = None

    urls: set[str] = set()
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                url = item.get(url_field)
                if isinstance(url, str) and url.startswith("http"):
                    urls.add(url)
    return urls


async def _step_fetch_json(
    step: dict[str, Any],
    client: httpx.AsyncClient,
    board_url: str,
) -> set[str]:
    url = step.get("url") or board_url
    items_path: str | None = step.get("items_path")
    url_field: str = step.get("url_field", "url")
    params: dict[str, Any] | None = step.get("params")

    try:
        kwargs: dict[str, Any] = {"follow_redirects": True}
        if params:
            kwargs["params"] = params
        resp = await client.get(url, **kwargs)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("inline.fetch_json.error", url=url, error=str(exc))
        return set()

    return _extract_urls_from_json(data, items_path, url_field)


async def _step_fetch_html(
    step: dict[str, Any],
    client: httpx.AsyncClient,
    board_url: str,
) -> set[str]:
    url = step.get("url") or board_url
    url_filter: str | None = step.get("url_filter")
    matcher = re.compile(url_filter, re.IGNORECASE) if url_filter else None

    try:
        resp = await client.get(url, follow_redirects=True)
        resp.raise_for_status()
        html = resp.text
    except Exception as exc:
        log.warning("inline.fetch_html.error", url=url, error=str(exc))
        return set()

    # Reuse the dom monitor's static link extractor (job-URL heuristics + JSON-LD
    # + regex). Imported lazily to keep both monitors' package-init order simple.
    from job_ftch.infrastructure.sources.monitors.dom import _extract_links_static

    return _extract_links_static(html, url, matcher)


async def discover(spec: Any, client: httpx.AsyncClient, auth: Any = None) -> set[str]:
    board_url = spec.url
    config = spec.monitor_config
    steps = config.get("steps", [])

    if not steps:
        log.warning("inline.no_steps", url=board_url)
        return set()
    if not isinstance(steps, list):
        log.warning("inline.invalid_steps", url=board_url, steps_type=type(steps).__name__)
        return set()

    all_urls: set[str] = set()
    for step in steps:
        if not isinstance(step, dict):
            log.warning("inline.invalid_step", url=board_url, step_type=type(step).__name__)
            continue
        step_type = step.get("type", "")
        if step_type == "fetch_json":
            urls = await _step_fetch_json(step, client, board_url)
        elif step_type == "fetch_html":
            urls = await _step_fetch_html(step, client, board_url)
        else:
            log.warning("inline.unknown_step_type", step_type=step_type, url=board_url)
            continue

        log.debug("inline.step_done", step_type=step_type, url=board_url, found=len(urls))
        all_urls.update(urls)

        if len(all_urls) >= _MAX_URLS:
            log.warning("inline.cap_reached", url=board_url, cap=_MAX_URLS)
            break

    # Never return the board page itself as a job URL.
    normalized_board = board_url.rstrip("/")
    return {u for u in all_urls if u.rstrip("/") != normalized_board}


register_monitor("inline", discover, cost=60, rich=False)
