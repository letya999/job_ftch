from __future__ import annotations

import asyncio

import pytest

from job_ftch.application.run_budget import (
    AsyncCallBudget,
    BudgetOutcome,
    HierarchicalBudget,
    ScopedCircuitBreaker,
)


@pytest.mark.asyncio
async def test_hierarchical_budget_reserves_all_scopes_or_none() -> None:
    budget = HierarchicalBudget({"run": 2, "tenant": 1, "source": 2, "stage": 2, "item": 2})
    scopes = ("run", "tenant", "source", "stage", "item")

    first = await budget.reserve(scopes)
    second = await budget.reserve(scopes)

    assert first.outcome is BudgetOutcome.EXECUTED
    assert second == (await budget.reserve(scopes))
    assert second.outcome is BudgetOutcome.BUDGET_EXHAUSTED
    assert second.scope == "tenant"
    assert budget.used == {"run": 1, "tenant": 1, "source": 1, "stage": 1, "item": 1}


@pytest.mark.asyncio
async def test_async_call_budget_reports_explicit_exhaustion() -> None:
    budget = AsyncCallBudget(1)

    assert (await budget.reserve(scope="extraction")).outcome is BudgetOutcome.EXECUTED
    assert (await budget.reserve(scope="extraction")).outcome is BudgetOutcome.BUDGET_EXHAUSTED
    assert await budget.try_acquire() is False


@pytest.mark.asyncio
async def test_concurrent_reservations_never_exceed_limit() -> None:
    budget = AsyncCallBudget(3)
    results = await asyncio.gather(*(budget.reserve() for _ in range(20)))

    assert sum(result.acquired for result in results) == 3


@pytest.mark.asyncio
async def test_circuit_breaker_is_scoped_and_recovers_on_success() -> None:
    breaker = ScopedCircuitBreaker(failure_threshold=2, conewown_seconds=60)
    key = {"provider": "llm", "stage": "extract", "tenant": "a"}

    await breaker.record_failure(**key)
    await breaker.record_failure(**key)

    assert await breaker.allow(**key) is False
    assert await breaker.allow(provider="llm", stage="extract", tenant="b") is True
    await breaker.record_success(**key)
    assert await breaker.allow(**key) is True
