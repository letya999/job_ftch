"""Auto-detection for career site monitors based on URL probes."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import parse_qsl, urlparse

import structlog

from job_ftch.application.registry import get_all_monitor_entries
from job_ftch.infrastructure.sources.site_fingerprinter import fingerprint

logger = structlog.get_logger(__name__)


def _prefer_listing_monitor_for_filtered_url(url: str, ordered: list[str]) -> list[str]:
    """Keep filtered search pages scoped instead of expanding a whole sitemap."""
    query_keys = set(dict(parse_qsl(urlparse(url).query, keep_blank_values=True)))
    filter_keys = {"q", "query", "text", "keywords", "search", "filter", "filters"}
    if not query_keys & filter_keys or "dom" not in ordered or "sitemap" not in ordered:
        return ordered
    return ["dom", *[name for name in ordered if name != "dom"]]


async def detect_monitor_type(url: str, client: Any) -> tuple[str, dict[str, Any]] | None:
    """
    Determines optimal monitor for a URL.
    Uses SiteFingerprinter for fast classification, falls back to can_handle iteration.
    """
    try:
        profile = await fingerprint(url, client)
        if profile.recommended_monitors:
            # Return the first recommended monitor and any detected config hints
            return profile.recommended_monitors[0], profile.detected_config
    except Exception as e:
        logger.warning("fingerprint_failed_falling_back", url=url, error=str(e))

    # Fallback to legacy can_handle() iteration
    for entry in get_all_monitor_entries():
        if entry.can_handle is None:
            continue
        try:
            # can_handle is async (url, client) -> dict | None
            result = await entry.can_handle(url, client)
            if result is not None:
                return entry.name, result
        except Exception:
            # Skip if detection fails
            continue
    return None


async def get_ordered_monitors(
    url: str, client: Any
) -> tuple[list[str], dict[str, Any], str | None]:
    """Returns full ordered monitor list, detected runtime config hints, and canonical URL."""
    from job_ftch.infrastructure.sources.site_fingerprinter import (
        _probe_client_with_stealth_fallback,
        known_monitor_from_url,
    )

    known = known_monitor_from_url(url)
    if known is not None:
        return [known], {}, None

    try:
        profile = await fingerprint(url, client)
        ordered = list(profile.recommended_monitors)
        detected_config = dict(profile.detected_config)
        canonical_url = profile.canonical_url
        generic_monitors = {"dom", "api_sniffer"}

        # If fingerprint found an ATS but didn't extract config, give the monitor a chance to extract it
        if ordered and ordered[0] not in generic_monitors:
            primary_monitor = ordered[0]
            entries = get_all_monitor_entries()
            entry = next((e for e in entries if e.name == primary_monitor), None)
            if entry and entry.can_handle is not None:
                try:
                    async with _probe_client_with_stealth_fallback(client) as active_client:
                        result = await entry.can_handle(url, active_client)
                    if isinstance(result, dict):
                        detected_config = {**result, **detected_config}
                except Exception:
                    pass

        # If it's a generic monitor, we still iterate through all monitors to see if any can_handle it
        if set(ordered).issubset(generic_monitors):
            # ``sitemap`` is already a generic fallback, and its can_handle
            # performs a full sitemap walk.  Re-probing it here duplicates
            # network work before discovery has even started.  The remaining
            # ATS probes share one small total detection budget: a site must
            # not spend its whole source deadline in serial can_handle calls.
            from job_ftch.config import get_settings

            try:
                async with asyncio.timeout(get_settings().fingerprinter_timeout_seconds):
                    entries = get_all_monitor_entries()
                    # A Workday marker can occur late in a CMS page and is
                    # cheap to verify; probe it before slower ATS families so
                    # the shared detection budget is not exhausted first.
                    entries = sorted(entries, key=lambda entry: entry.name != "workday")
                    for entry in entries:
                        if entry.name in (*generic_monitors, "sitemap") or entry.can_handle is None:
                            continue
                        try:
                            async with _probe_client_with_stealth_fallback(client) as active_client:
                                result = await entry.can_handle(url, active_client)
                        except Exception:
                            continue
                        if result is None:
                            continue
                        ordered = [entry.name, *[name for name in ordered if name != entry.name]]
                        if isinstance(result, dict):
                            detected_config = {**result, **detected_config}
                        break
            except TimeoutError:
                logger.info("monitor_specific_detection_budget_exhausted", url=url)
        return (
            _prefer_listing_monitor_for_filtered_url(url, ordered),
            detected_config,
            canonical_url,
        )
    except Exception as e:
        logger.warning("get_ordered_monitors_failed", url=url, error=str(e))
        return ["dom", "api_sniffer"], {}, None
