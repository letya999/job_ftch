"""Auto-detection for career site monitors based on URL probes."""

from __future__ import annotations

from typing import Any

import structlog

from job_ftch.application.registry import get_all_monitor_entries
from job_ftch.infrastructure.sources.site_fingerprinter import fingerprint

logger = structlog.get_logger(__name__)


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


async def get_ordered_monitors(url: str, client: Any) -> list[str]:
    """Returns full ordered monitor list based on site fingerprint."""
    try:
        profile = await fingerprint(url, client)
        return profile.recommended_monitors
    except Exception as e:
        logger.warning("get_ordered_monitors_failed", url=url, error=str(e))
        return ["dom", "api_sniffer"]
