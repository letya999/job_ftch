"""Generic API-sniffer monitor for SPA career sites.

Captures JSON/XHR responses in a browser session and extracts either:
- rich payloads when the API returns full job objects;
- URL-only results when the API returns links to detail pages.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

import structlog

from job_ftch.application.registry import register_monitor
from job_ftch.domain.site_models import DiscoveredPostingPayload, MonitorResult
from job_ftch.infrastructure.sources.monitors.api_response_collector import (
    API_HINT_RE,
    BoundedResponseCollector,
    CapturedResponse,
)
from job_ftch.infrastructure.sources.source_deadline import sleep_with_source_deadline

if TYPE_CHECKING:
    import httpx

log = structlog.get_logger("job_ftch.monitors.api_sniffer")

_ABS_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_PATH_HINT_RE = re.compile(
    r"(/[a-z0-9\-_/]*(?:job|jobs|vacancy|vacancies|cariere|vakansiya|vakansiyalar)[a-z0-9\-_/]*)",
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


def _is_non_job_api_facet_url(value: str | None) -> bool:
    if not value:
        return False
    lowered = value.casefold()
    return (
        "api.hh." in lowered
        and "/vacancies?" in lowered
        and ("clusters=true" in lowered or "per_page=0" in lowered)
    )


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

    # Check for robust ID fields (Sber uses internalId/requisitionId)
    has_id = any(
        node.get(key)
        for key in (
            "vacancy_id",
            "vacancyId",
            "id",
            "jobId",
            "job_id",
            "internalId",
            "requisitionId",
            "guid",
            "listingID",
            "listingId",
            "reqId",
        )
    )

    raw_url = _pick_first(
        node,
        ("url", "jobUrl", "job_url", "absolute_url", "alternate_url", "viewUrl", "applyUrl"),
    )
    if isinstance(raw_url, str) and _is_non_job_api_facet_url(raw_url):
        return False
    return bool(has_url or has_desc or has_id)


def _payload_from_dict(node: dict[str, Any], base_url: str) -> DiscoveredPostingPayload | None:
    raw_url = _pick_first(
        node,
        ("url", "jobUrl", "job_url", "absolute_url", "alternate_url", "viewUrl", "applyUrl"),
    )
    if isinstance(raw_url, str):
        if _is_non_job_api_facet_url(raw_url):
            return None
        url = _normalize_url(raw_url, base_url)
        if url is None:
            return None
    else:
        job_id = _pick_first(
            node,
            (
                "vacancy_id",
                "vacancyId",
                "id",
                "jobId",
                "job_id",
                "internalId",
                "requisitionId",
                "guid",
                "listingID",
                "listingId",
                "reqId",
            ),
        )
        title = _pick_first(node, _TITLE_KEYS)
        if not (job_id and title):
            return None

        # Format a realistic URL if we can derive from base_url domain
        import urllib.parse

        parsed = urllib.parse.urlparse(base_url)
        if "sber.ru" in parsed.netloc:
            url = f"https://rabota.sber.ru/search/{job_id}/"
        elif "kaspi.kz" in parsed.netloc:
            url = f"https://job.kaspi.kz/vacancy/{job_id}"
        else:
            url = f"{base_url.rstrip('/')}/#job-{job_id}"

    title = _pick_first(node, _TITLE_KEYS)

    # Try all known description keys
    all_desc_keys = list(_DESC_KEYS) + ["duties", "requirements", "responsibilities"]
    description = _pick_first(node, tuple(all_desc_keys))
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
    if API_HINT_RE.search(resp.text):
        return {"browser": True}
    return None


async def discover(spec: Any, client: httpx.AsyncClient, auth: Any = None) -> MonitorResult:
    del auth
    try:
        from job_ftch.infrastructure.sources.browser_utils import (
            BROWSER_KEYS,
            open_page,
            run_actions,
        )
    except ImportError as exc:
        raise RuntimeError(
            "patchright is required for api_sniffer monitor. "
            "Install: pip install patchright && patchright install chromium"
        ) from exc

    config = dict(spec.monitor_config or {})
    board_url = spec.url
    browser_config = {k: v for k, v in config.items() if k in BROWSER_KEYS}
    bypass_strategy = config.get("_bypass_strategy")
    from job_ftch.config import get_settings as _get_settings

    _settings = _get_settings()
    settle_seconds = float(config.get("settle_seconds", _settings.api_sniffer_settle_seconds))
    api_pattern = config.get("api_url_match")
    collector = BoundedResponseCollector(
        max_responses=int(config.get("max_responses", _settings.api_sniffer_max_responses)),
        max_single_bytes=int(
            config.get("max_response_bytes", _settings.api_sniffer_max_response_bytes)
        ),
        max_total_bytes=int(config.get("max_total_bytes", _settings.api_sniffer_max_total_bytes)),
        decode_concurrency=int(
            config.get("decode_concurrency", _settings.api_sniffer_decode_concurrency)
        ),
        api_pattern=str(api_pattern) if api_pattern else None,
    )

    async def _block_trackers(route: Any) -> None:
        url = route.request.url.lower()
        from job_ftch.infrastructure.network.ssrf_guard import check_ssrf
        from job_ftch.infrastructure.sources.source_deadline import await_with_source_deadline

        try:
            # Route callbacks run in their own Patchright tasks.  Keep the
            # DNS-backed SSRF guard inside the source budget; otherwise
            # ``page.unroute_all(behavior='wait')`` can hang during teardown
            # waiting for a resolver that outlived its source.
            await await_with_source_deadline(check_ssrf(url))
        except Exception:
            try:
                await route.abort()
            except Exception as exc:
                if type(exc).__name__ != "TargetClosedError":
                    raise
            return

        if any(
            t in url
            for t in [
                "mc.yandex.ru",
                "vk.com",
                "vkvideo.ru",
                "ads.linkedin.com",
                "apptracer.ru",
                "google-analytics.com",
                "facebook.com",
                "metrika",
                "sentry.io",
            ]
        ):
            route_operation = route.abort
        else:
            route_operation = route.continue_

        try:
            await route_operation()
        except Exception as exc:
            # A page can be closed while Patchright is dispatching an
            # in-flight route callback during deadline teardown.  That is a
            # normal cancellation outcome, but allowing it to escape the
            # listener leaves an orphan Future and emits noisy
            # ``TargetClosedError`` diagnostics after the source is complete.
            if type(exc).__name__ == "TargetClosedError" or "Route is already handled" in str(exc):
                return
            raise

    async with collector, open_page(browser_config, bypass_strategy=bypass_strategy) as page:
        if hasattr(page, "route"):
            await page.route("**/*", _block_trackers)
        page.on("response", collector.schedule)

        from job_ftch.infrastructure.network.ssrf_guard import check_ssrf

        await check_ssrf(board_url)
        await page.goto(board_url, wait_until=config.get("wait", "domcontentloaded"))
        actions = config.get("actions")
        if actions:
            await run_actions(page, actions)

        # Replace fixed sleep with networkidle
        from job_ftch.config import get_settings

        try:
            await page.wait_for_load_state(
                "networkidle", timeout=get_settings().browser_default_timeout_ms
            )
        except Exception:
            await sleep_with_source_deadline(settle_seconds)

        await collector.drain()

        payloads: dict[str, DiscoveredPostingPayload] = {}
        urls: set[str] = set()
        discovered_api_endpoint: str | None = None

        for captured in collector.payloads:
            payloads.update(_collect_payloads(captured.data, board_url))
            urls.update(_collect_urls(captured.data, board_url))

        # Auto-routing if no items found on the landing page
        if not payloads and not urls:
            try:
                locator = page.locator(
                    "a:has-text('вакансии'), a:has-text('Вакансии'), a:has-text('vacancies'), a:has-text('Vacancies'), a:has-text('Vakansiyalar'), a:has-text('Cariere'), a[href*='/vacancies'], a[href*='/vacancy'], a[href*='/vakansiya'], a[href*='/cariere'], a[href*='/jobs'], a[href*='/job'], a[href*='/search'], a[href*='/openings'], a[href*='/career']"
                ).first
                if await locator.count() > 0:
                    href = await locator.get_attribute("href")
                    if href:
                        log.info("api_sniffer_auto_route", url=board_url, route=href)
                        await locator.click(timeout=5000)
                        try:
                            await page.wait_for_load_state(
                                "networkidle", timeout=get_settings().browser_default_timeout_ms
                            )
                        except Exception:
                            await sleep_with_source_deadline(settle_seconds)

                        # Re-collect newly captured payloads
                        await collector.drain()
                        for captured in collector.payloads:
                            payloads.update(_collect_payloads(captured.data, board_url))
                            urls.update(_collect_urls(captured.data, board_url))
            except Exception as exc:
                log.warning("api_sniffer_failed", error=str(exc))

        # Attempt to extract embedded JSON from the rendered HTML
        # This catches jobs on SPAs (like Ozon, Lamoda) that load via SSR
        # and don't trigger XHRs on initial load.
        if hasattr(page, "content"):
            html = await page.content()
        else:
            html = ""
        from job_ftch.infrastructure.sources.monitors.shared import (
            check_ats_redirect,
            raise_if_browser_challenge,
        )

        raise_if_browser_challenge(html, url=board_url)
        check_ats_redirect(html, board_url)

        from job_ftch.infrastructure.sources.embedded_state_utils import (
            extract_inertia_data,
            extract_next_data,
            extract_nuxt_data,
            extract_phenom_canvas_data,
            extract_pinia_state,
            extract_react_router_data,
            extract_rsc_data,
            resolve_path,
        )
        from job_ftch.infrastructure.sources.monitors.nextdata import (
            _build_url,
            _find_jobs_path,
            _heuristic_extract_job,
        )

        extractors = [
            extract_next_data,
            extract_nuxt_data,
            extract_pinia_state,
            extract_inertia_data,
            extract_react_router_data,
            extract_rsc_data,
            extract_phenom_canvas_data,
        ]

        for extractor in extractors:
            data = extractor(html)
            if data:
                found = _find_jobs_path(data)
                if found:
                    path, _ = found
                    items = resolve_path(data, path)
                    if isinstance(items, list):
                        for item in items:
                            if not isinstance(item, dict):
                                continue
                            url = _build_url(item, None, None, board_url=board_url)
                            if not url:
                                if "id" in item:
                                    url = f"{board_url.rstrip('/')}/job/{item['id']}"
                                elif "slug" in item:
                                    url = f"{board_url.rstrip('/')}/job/{item['slug']}"
                                else:
                                    continue
                            payload = _heuristic_extract_job(item, url)
                            # Ensure we don't duplicate payloads
                            if payload.url not in payloads:
                                payloads[payload.url] = payload
                                urls.add(payload.url)
                                collector.payloads.append(
                                    CapturedResponse(url=board_url, data={"items": [item]})
                                )

        await collector.drain()
        if collector.truncated:
            log.warning(
                "api_sniffer_capture_truncated",
                captured=len(collector.payloads),
                total_bytes=collector.total_bytes,
            )

        # Auto-paginate if we discovered an API and have client access
        if payloads and collector.payloads:
            import urllib.parse

            from job_ftch.infrastructure.sources.browser_utils import fetch_in_page

            # Try to find the API request that yielded payloads
            primary_capture: CapturedResponse | None = None
            for captured in collector.payloads:
                if _collect_payloads(captured.data, board_url):
                    primary_capture = captured
                    break

            if primary_capture and primary_capture.method == "GET":
                primary_api = primary_capture.url
                discovered_api_endpoint = primary_api
                parsed_api = urllib.parse.urlparse(primary_api)
                qs = urllib.parse.parse_qs(parsed_api.query)

                # Find a pagination param
                page_param = None
                page_val = 0
                page_size = 50
                for param in ("skip", "page", "pageNo", "offset", "startIndex", "p"):
                    if param in qs:
                        page_param = param
                        try:
                            page_val = int(qs[param][0])
                            if param in ("skip", "offset", "startIndex"):
                                for size_param in ("take", "limit", "pageSize"):
                                    if size_param in qs:
                                        page_size = int(qs[size_param][0])
                                        break
                                else:
                                    page_size = _settings.api_sniffer_default_page_size
                        except ValueError:
                            pass
                        break

                if page_param:
                    safe_api_url = urllib.parse.urlunparse(
                        parsed_api._replace(query="", fragment="")
                    )
                    log.info("api_sniffer_auto_paginate", url=safe_api_url, param=page_param)
                    max_pages = _settings.api_sniffer_max_pages
                    for _ in range(max_pages):
                        if page_param in ("skip", "offset", "startIndex"):
                            page_val += page_size
                        else:
                            page_val += 1

                        qs[page_param] = [str(page_val)]
                        new_query = urllib.parse.urlencode(qs, doseq=True)
                        next_url = urllib.parse.urlunparse(parsed_api._replace(query=new_query))

                        try:
                            await sleep_with_source_deadline(1.0)
                            # In-page fetch bypasses header checks and signed API restrictions
                            resp = await fetch_in_page(page, next_url)
                            if resp.get("status") == 200:
                                page_data = json.loads(resp.get("text", "{}"))
                                new_payloads = _collect_payloads(page_data, board_url)
                                if not new_payloads:
                                    break
                                payloads.update(new_payloads)
                                urls.update(_collect_urls(page_data, board_url))
                            else:
                                break
                        except Exception as e:
                            log.debug("api_sniffer_pagination_error", error=str(e))
                            break

    if payloads:
        payload_urls = set(payloads.keys())
        return MonitorResult(
            urls=payload_urls,
            payloads_by_url=payloads,
            metadata_updates=(
                {"api_endpoint": discovered_api_endpoint} if discovered_api_endpoint else {}
            ),
        )
    return MonitorResult(
        urls=urls,
        metadata_updates=(
            {"api_endpoint": discovered_api_endpoint} if discovered_api_endpoint else {}
        ),
    )


register_monitor("api_sniffer", discover, cost=200, rich=True, can_handle=can_handle)
