"""Async-safe reservation budgets with explicit execution outcomes."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


class BudgetOutcome(StrEnum):
    EXECUTED = "executed"
    CACHED = "cached"
    BUDGET_EXHAUSTED = "budget_exhausted"
    PROVIDER_FAILED = "provider_failed"
    DEFERRED = "deferred"


@dataclass(frozen=True, slots=True)
class BudgetReservation:
    outcome: BudgetOutcome
    scope: str

    @property
    def acquired(self) -> bool:
        return self.outcome is BudgetOutcome.EXECUTED


class AsyncCallBudget:
    """Concurrency-safe call limiter shared by multiple pipeline nodes."""

    def __init__(self, limit: int) -> None:
        if limit < 0:
            msg = "AsyncCallBudget limit must be >= 0."
            raise ValueError(msg)
        self._limit = limit
        self._used = 0
        self._lock = asyncio.Lock()

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def used(self) -> int:
        return self._used

    async def try_acquire(self) -> bool:
        return (await self.reserve()).acquired

    async def reserve(self, *, scope: str = "run") -> BudgetReservation:
        async with self._lock:
            if self._used >= self._limit:
                return BudgetReservation(BudgetOutcome.BUDGET_EXHAUSTED, scope)
            self._used += 1
            return BudgetReservation(BudgetOutcome.EXECUTED, scope)

    async def release(self) -> None:
        """Return an unused reservation, for example when a source is exhausted."""
        async with self._lock:
            if self._used == 0:
                raise RuntimeError("Cannot release an empty AsyncCallBudget.")
            self._used -= 1


class HierarchicalBudget:
    """Atomically reserve one unit across run/tenant/source/stage/item scopes.

    All scopes share one lock, so a failed child/parent check consumes nothing.
    This small primitive intentionally tracks calls only; token/money/time
    reservations can be layered using the same interface without changing
    node call sites.
    """

    def __init__(self, limits: dict[str, int]) -> None:
        if not limits:
            raise ValueError("HierarchicalBudget requires at least one scope.")
        if any(limit < 0 for limit in limits.values()):
            raise ValueError("Budget limits must be >= 0.")
        self._limits = dict(limits)
        self._used = {scope: 0 for scope in limits}
        self._lock = asyncio.Lock()

    @property
    def used(self) -> dict[str, int]:
        return dict(self._used)

    async def reserve(self, scopes: tuple[str, ...]) -> BudgetReservation:
        unknown = [scope for scope in scopes if scope not in self._limits]
        if unknown:
            raise KeyError(f"Unknown budget scope(s): {', '.join(unknown)}")
        async with self._lock:
            exhausted = next(
                (scope for scope in scopes if self._used[scope] >= self._limits[scope]),
                None,
            )
            if exhausted is not None:
                return BudgetReservation(BudgetOutcome.BUDGET_EXHAUSTED, exhausted)
            for scope in scopes:
                self._used[scope] += 1
            return BudgetReservation(BudgetOutcome.EXECUTED, scopes[-1])


@dataclass(slots=True)
class _CircuitState:
    failures: int = 0
    open_until: float = 0.0
    probe_in_flight: bool = False


class ScopedCircuitBreaker:
    """Failure circuit scoped to one provider/stage/tenant tuple."""

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        conewown_seconds: float = 30.0,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if failure_threshold < 1 or conewown_seconds < 0:
            raise ValueError("Circuit breaker threshold/conewown must be non-negative.")
        self._failure_threshold = failure_threshold
        self._conewown_seconds = conewown_seconds
        self._clock = clock
        self._states: dict[tuple[str, str, str], _CircuitState] = {}
        self._lock = asyncio.Lock()

    async def allow(self, *, provider: str, stage: str, tenant: str) -> bool:
        key = (provider, stage, tenant)
        async with self._lock:
            state = self._states.get(key)
            if state is None:
                return True
            if state.open_until == 0.0:
                return True
            if self._clock() < state.open_until:
                return False
            if state.probe_in_flight:
                return False
            # After conewown, permit a single half-open probe.  Its success
            # resets the scoped state; its failure opens only this scope again.
            state.probe_in_flight = True
            return True

    async def record_success(self, *, provider: str, stage: str, tenant: str) -> None:
        async with self._lock:
            self._states.pop((provider, stage, tenant), None)

    async def record_failure(self, *, provider: str, stage: str, tenant: str) -> None:
        key = (provider, stage, tenant)
        async with self._lock:
            state = self._states.setdefault(key, _CircuitState())
            state.failures += 1
            state.probe_in_flight = False
            if state.failures >= self._failure_threshold:
                state.open_until = self._clock() + self._conewown_seconds
