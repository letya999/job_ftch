"""Per-domain token-bucket rate limiter with adaptive tightening on 429."""

from __future__ import annotations

import asyncio
import random
import time
from typing import TypedDict

from job_ftch.config import get_settings
from job_ftch.infrastructure.bypass.domain_intel import get_domain_intel


class _BucketState(TypedDict):
    tokens: float
    last_update: float
    rate: float
    penalty_until: float
    lock: asyncio.Lock


class DomainPacer:
    """Per-domain token-bucket rate limiter with adaptive tightening on 429."""

    def __init__(self, default_rate: float):
        self.default_rate = default_rate
        # domain -> {"tokens": float, "last_update": float, "rate": float, "penalty_until": float, "lock": asyncio.Lock}
        self._buckets: dict[str, _BucketState] = {}
        self._global_lock = asyncio.Lock()

        # Integration with domain_intel
        self._intel = get_domain_intel()
        for domain, entry in self._intel._entries.items():
            if entry.rate_limit_rpm > 0:
                state = self._get_bucket_state_sync(domain)
                state["rate"] = min(self.default_rate, entry.rate_limit_rpm / 60.0)

    def _get_bucket_state_sync(self, domain: str) -> _BucketState:
        if domain not in self._buckets:
            self._buckets[domain] = {
                "tokens": 1.0,
                "last_update": time.monotonic(),
                "rate": self.default_rate,
                "penalty_until": 0.0,
                "lock": asyncio.Lock(),
            }
        return self._buckets[domain]

    async def _get_bucket_state(self, domain: str) -> _BucketState:
        if domain not in self._buckets:
            async with self._global_lock:
                if domain not in self._buckets:
                    self._buckets[domain] = {
                        "tokens": 1.0,
                        "last_update": time.monotonic(),
                        "rate": self.default_rate,
                        "penalty_until": 0.0,
                        "lock": asyncio.Lock(),
                    }
        return self._buckets[domain]

    async def acquire(self, domain: str) -> float:
        """Wait until a request to domain is allowed. Returns seconds waited."""
        domain = domain.lower()
        state = await self._get_bucket_state(domain)

        async with state["lock"]:
            now = time.monotonic()

            if state["penalty_until"] > 0 and now >= state["penalty_until"]:
                state["penalty_until"] = 0.0

            elapsed = now - state["last_update"]
            state["tokens"] = min(1.0, state["tokens"] + elapsed * state["rate"])
            state["last_update"] = now

            wait_time = 0.0
            if state["tokens"] < 1.0:
                wait_time = (1.0 - state["tokens"]) / state["rate"]
                state["tokens"] = 1.0
                state["last_update"] = now + wait_time

            state["tokens"] -= 1.0

            jitter = self._poisson_jitter(state["rate"])
            total_wait = wait_time + jitter

            if total_wait > 0:
                await asyncio.sleep(total_wait)

            return total_wait

    def _poisson_jitter(self, rate: float) -> float:
        """Small random jitter for natural inter-arrival spacing."""
        return random.uniform(0, 0.3)

    def apply_temporal_hint(self, domain: str, pacing_seconds: float) -> None:
        """Adjust domain rate from orchestrator's temporal_pacing_seconds hint."""
        if pacing_seconds <= 0:
            return
        domain = domain.lower()
        hint_rate = 1.0 / pacing_seconds
        state = self._get_bucket_state_sync(domain)
        state["rate"] = min(state["rate"], hint_rate)

    def record_rate_limit(self, domain: str, retry_after: float | None = None) -> None:
        """Tighten the domain's bucket after a 429."""
        domain = domain.lower()
        state = self._get_bucket_state_sync(domain)

        state["rate"] /= 2.0
        state["tokens"] = 0.0

        penalty = retry_after if retry_after is not None else 600.0
        state["penalty_until"] = time.monotonic() + penalty
        state["last_update"] = time.monotonic()

        entry = self._intel.get(domain)
        entry.rate_limit_rpm = int(state["rate"] * 60)
        self._intel._dirty = True

    def record_success(self, domain: str) -> None:
        """Gradually restore the domain's rate toward default."""
        domain = domain.lower()
        state = self._get_bucket_state_sync(domain)

        if state["penalty_until"] > 0 and time.monotonic() < state["penalty_until"]:
            return

        if state["rate"] < self.default_rate:
            alpha = 0.1
            state["rate"] = (1 - alpha) * state["rate"] + alpha * self.default_rate

            entry = self._intel.get(domain)
            entry.rate_limit_rpm = int(state["rate"] * 60)
            self._intel._dirty = True


_pacer_instance: DomainPacer | None = None


def get_domain_pacer() -> DomainPacer:
    global _pacer_instance
    if _pacer_instance is None:
        settings = get_settings()
        rate = getattr(settings, "bypass_default_requests_per_second", 2.0)
        _pacer_instance = DomainPacer(default_rate=rate)
    return _pacer_instance
