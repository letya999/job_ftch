"""Auto-detection for career site monitors based on URL probes."""

from __future__ import annotations

from typing import Any

from job_ftch.application.registry import get_all_monitor_entries


async def detect_monitor_type(url: str, client: Any) -> tuple[str, dict[str, Any]] | None:
    """Iterate registered monitors sorted by cost, call can_handle() on each."""
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
