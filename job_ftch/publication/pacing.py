"""Drip-publication pacing.

Splits a job queue into randomised mini-batches spread across a time window,
so publications feel organic rather than a single burst.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PacingConfig:
    window_seconds: int = 14400
    target_per_window: int = 12
    burst_min: int = 2
    burst_max: int = 4
    gap_min_seconds: int = 120
    gap_max_seconds: int = 1500
    spread_fraction: float = 0.6
    msg_throttle_seconds: float = 3.5


@dataclass
class Burst:
    items: list[int] = field(default_factory=list)
    offset_seconds: float = 0.0


def plan_bursts(
    queue_size: int,
    config: PacingConfig | None = None,
) -> list[Burst]:
    """Plan a sequence of bursts for a given queue size.

    Returns a list of Burst objects, each with item indices and a time offset
    from the window start. The caller is responsible for scheduling actual sends.
    """
    if queue_size <= 0:
        return []

    cfg = config or PacingConfig()
    to_send = min(queue_size, cfg.target_per_window)
    if to_send <= 0:
        return []

    bursts: list[Burst] = []
    idx = 0
    usable_window = cfg.window_seconds * cfg.spread_fraction
    offset = 0.0

    while idx < to_send:
        burst_size = min(
            random.randint(cfg.burst_min, cfg.burst_max),
            to_send - idx,
        )
        items = list(range(idx, idx + burst_size))
        bursts.append(Burst(items=items, offset_seconds=offset))
        idx += burst_size
        if idx < to_send:
            gap = random.uniform(cfg.gap_min_seconds, cfg.gap_max_seconds)
            offset += gap
            if offset > usable_window:
                offset = usable_window

    return bursts
