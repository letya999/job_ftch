from __future__ import annotations

import asyncio

import pytest
from structlog.testing import capture_logs

from job_ftch.infrastructure.bypass.attempt_budget import AttemptBudget


def _budget(**overrides: int) -> AttemptBudget:
    values = {
        "max_route_attempts": 2,
        "max_source_browser_launches": 3,
        "max_source_proxy_rotations": 2,
        "max_weighted_work": 100,
        "max_proxy_rotations_per_operation": 1,
    }
    values.update(overrides)
    return AttemptBudget(**values)


def test_nested_limits_bound_route_browser_and_proxy_work() -> None:
    budget = _budget()
    token = budget.start_operation("listing", kind="listing", max_browser_launches=1)
    try:
        assert budget.allow_route_transition(weight=1)
        budget.note_route_transition(weight=1)
        assert budget.allow_route_transition(weight=1)
        budget.note_route_transition(weight=1)
        assert not budget.allow_route_transition(weight=1)

        assert budget.allow_browser_launch()
        budget.note_browser_launch()
        assert not budget.allow_browser_launch()

        assert budget.allow_proxy_rotation()
        budget.note_proxy_rotation()
        assert not budget.allow_proxy_rotation()
    finally:
        budget.end_operation(token)


@pytest.mark.asyncio
async def test_detail_operation_budget_is_task_local() -> None:
    budget = _budget(max_source_browser_launches=2)

    async def _detail(identifier: str) -> tuple[bool, bool]:
        token = budget.start_operation(identifier, kind="detail", max_browser_launches=1)
        try:
            first = budget.allow_browser_launch()
            budget.note_browser_launch()
            await asyncio.sleep(0)
            return first, budget.allow_browser_launch()
        finally:
            budget.end_operation(token)

    assert await asyncio.gather(_detail("one"), _detail("two")) == [
        (True, False),
        (True, False),
    ]


def test_new_domain_does_not_preselect_a_non_noop_engine() -> None:
    from job_ftch.infrastructure.bypass.risk_router import RiskRouter

    assert RiskRouter().select_tier("https://fresh.example.test/jobs") == "noop"


def test_attempt_log_hashes_url_and_omits_query_credentials() -> None:
    budget = _budget()
    with capture_logs() as logs:
        budget.log_attempt(
            source_id="https://user:password@example.test/jobs?token=secret",
            transport="httpx",
            browser=None,
            network="direct",
            failure_kind="blocked",
            status_code=403,
        )
    rendered = str(logs)
    assert "example.test" not in rendered
    assert "password" not in rendered
    assert "secret" not in rendered


def test_attempt_ledger_counts_repeats_and_emits_complete_route_fields() -> None:
    budget = _budget()
    token = budget.start_operation("detail-1", kind="detail", max_browser_launches=1)
    try:
        with capture_logs() as logs:
            for _ in range(2):
                budget.log_attempt(
                    source_id="https://example.test/jobs/1",
                    transport="httpx",
                    browser="chromium",
                    network="direct",
                    failure_kind="server_error",
                    status_code=503,
                    session_generation=2,
                    challenge_action="none",
                )
    finally:
        budget.end_operation(token)

    final = logs[-1]
    assert final["attempt_number"] == 2
    assert final["same_route_retry_count"] == 1
    assert final["session_generation"] == 2
    assert final["challenge_action"] == "none"
    assert isinstance(final["elapsed_ms"], int)
    assert len(final["url_hash"]) == 16
