"""Helpers for choosing safe concurrency levels at runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass
from math import ceil
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

_SOURCE_TYPE_WEIGHTS: dict[str, float] = {
    "local_fixture": 1.0,
    "telegram_channel": 1.0,
    "telegram_group": 1.0,
    "telegram_comments": 1.0,
    "telegram_realtime": 1.0,
    "rss_feed": 1.0,
    "lever": 1.0,
    "rest_api": 1.5,
    "declarative_html": 2.0,
    "career_site": 3.0,
    "browser": 4.0,
    "webhook": 1.0,
    "websocket": 1.0,
}


@dataclass(frozen=True, slots=True)
class ConcurrencyPlan:
    requested: int
    effective: int
    source_count: int
    source_work_units: int
    cpu_count: int
    adaptive: bool
    source_cap: int
    cpu_cap: int
    backend_cap: int | None = None


def estimate_source_work_units(source_specs: Sequence[Any]) -> int:
    if not source_specs:
        return 1
    total = 0.0
    for spec in source_specs:
        source_type = str(getattr(spec, "type", "") or "").strip()
        total += _SOURCE_TYPE_WEIGHTS.get(source_type, 1.0)
    return max(1, ceil(total))


def resolve_source_fetch_concurrency(
    *,
    requested: int,
    source_count: int,
    source_work_units: int,
    adaptive: bool,
    cpu_count: int | None = None,
) -> ConcurrencyPlan:
    return _resolve_concurrency(
        requested=requested,
        source_count=source_count,
        source_work_units=source_work_units,
        adaptive=adaptive,
        cpu_count=cpu_count,
        source_multiplier=1,
        cpu_multiplier=2,
        min_floor=1,
        backend_cap=None,
    )


def resolve_source_preparation_concurrency(
    *,
    requested: int,
    source_count: int,
    source_work_units: int,
    adaptive: bool,
    cpu_count: int | None = None,
) -> ConcurrencyPlan:
    return _resolve_concurrency(
        requested=requested,
        source_count=source_count,
        source_work_units=source_work_units,
        adaptive=adaptive,
        cpu_count=cpu_count,
        source_multiplier=1,
        cpu_multiplier=2,
        min_floor=1,
        backend_cap=None,
    )


def resolve_pipeline_item_concurrency(
    *,
    requested: int,
    source_count: int,
    source_work_units: int,
    store_pool_max: int,
    adaptive: bool,
    cpu_count: int | None = None,
) -> ConcurrencyPlan:
    return _resolve_concurrency(
        requested=requested,
        source_count=source_count,
        source_work_units=source_work_units,
        adaptive=adaptive,
        cpu_count=cpu_count,
        source_multiplier=2,
        cpu_multiplier=4,
        min_floor=4,
        backend_cap=max(4, int(store_pool_max)),
    )


def _resolve_concurrency(
    *,
    requested: int,
    source_count: int,
    source_work_units: int,
    adaptive: bool,
    cpu_count: int | None,
    source_multiplier: int,
    cpu_multiplier: int,
    min_floor: int,
    backend_cap: int | None,
) -> ConcurrencyPlan:
    requested_cap = max(1, int(requested))
    normalized_source_count = max(1, int(source_count))
    normalized_source_work_units = max(1, int(source_work_units))
    normalized_cpu = max(1, int(cpu_count or os.cpu_count() or 1))
    source_cap = max(min_floor, normalized_source_work_units * source_multiplier)
    cpu_cap = max(min_floor, normalized_cpu * cpu_multiplier)

    effective = requested_cap
    if adaptive:
        effective = min(requested_cap, source_cap, cpu_cap)
        if backend_cap is not None:
            effective = min(effective, backend_cap)

    return ConcurrencyPlan(
        requested=requested_cap,
        effective=effective,
        source_count=normalized_source_count,
        source_work_units=normalized_source_work_units,
        cpu_count=normalized_cpu,
        adaptive=adaptive,
        source_cap=source_cap,
        cpu_cap=cpu_cap,
        backend_cap=backend_cap,
    )
