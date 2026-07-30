"""One monitor attempt using the source's effective HTTP/context configuration."""

from __future__ import annotations

from typing import Any

from job_ftch.infrastructure.sources.career_site import client_for_config
from job_ftch.infrastructure.sources.site_utils import normalize_monitor_result
from job_ftch.infrastructure.sources.source_deadline import await_with_source_deadline


async def run_monitor_attempt(
    monitor_entry: Any,
    *,
    monitor_spec: Any,
    http: Any,
    auth: Any,
    monitor_config: dict[str, Any],
) -> Any:
    """Construct, execute and normalize exactly one registered monitor."""
    async with client_for_config(http, monitor_config) as monitor_http:
        monitor_instance = monitor_entry.factory(
            monitor_spec,
            monitor_http,
            auth,
        )
        if hasattr(monitor_instance, "discover"):
            raw_result = await await_with_source_deadline(
                monitor_instance.discover(monitor_spec, monitor_http)
            )
        else:
            raw_result = await await_with_source_deadline(monitor_instance)
    return normalize_monitor_result(raw_result)
