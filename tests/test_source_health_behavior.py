from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from job_ftch.application.pipeline import SourceRunStats
from job_ftch.application.tenant_runner import TenantRunner, _update_source_health_payload
from job_ftch.config import Settings
from job_ftch.domain import SourceHealth, TenantConfig, source_spec_identifier

if TYPE_CHECKING:
    from pathlib import Path


def _write_fixture(path: Path) -> None:
    item = {
        "source_kind": "local_fixture",
        "source_name": "fixture",
        "external_id": "1",
        "text": "test",
        "metadata": {},
    }
    path.write_text(json.dumps([item]), encoding="utf-8")


@pytest.mark.asyncio
async def test_source_health_auto_pause_and_probe_lifecycle(tmp_path: Path) -> None:
    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)

    tenant_id = "test_health"
    tenant = TenantConfig.model_validate(
        {
            "tenant_id": tenant_id,
            "display_name": "Health Test",
            "sources": [{"type": "local_fixture", "path": fixture_path.as_posix()}],
            "store_backend": "sqlite",
            "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
            "job_backend": "sqlite",
            "search_backend": "sqlite",
        }
    )

    settings = Settings()
    settings.source_health_failure_streak_pause = 2
    settings.source_health_probe_every_n_runs = 3

    runner = TenantRunner.from_tenants([tenant], base_settings=settings)
    runtime = runner.get_runtime(tenant_id)
    sid = source_spec_identifier(runtime.base_sources[0])

    now = datetime.now(UTC)
    health = SourceHealth(
        source_id=sid,
        source_kind="local_fixture",
        source_name="fixture",
        last_run_at=(now - timedelta(minutes=10)).isoformat(),
        last_success_at=None,
        failure_streak=2,
        success_count=0,
        last_fetched=0,
        last_emitted=0,
        last_failed=1,
        last_quarantined=0,
        baseline_emitted=0.0,
        drift_ratio=None,
        degraded=False,
        status="paused",
        paused=True,
        skipped_runs=0,
    )
    await runtime.store.save_source_health(sid, health)

    # Run 1: Should be skipped
    summary1 = await runner.run_tenant(tenant_id)
    assert sid not in summary1.by_source_id
    health_after1 = await runtime.store.get_source_health(sid)
    assert health_after1.paused is True
    assert health_after1.skipped_runs == 1

    # Run 2: Should be skipped
    summary2 = await runner.run_tenant(tenant_id)
    assert sid not in summary2.by_source_id
    health_after2 = await runtime.store.get_source_health(sid)
    assert health_after2.skipped_runs == 2

    # Run 3: Should PROBE (skipped_runs reaches 3)
    summary3 = await runner.run_tenant(tenant_id)
    assert sid in summary3.by_source_id
    health_after3 = await runtime.store.get_source_health(sid)
    assert health_after3.skipped_runs == 0
    assert health_after3.paused is False
    assert health_after3.failure_streak == 0

    await runner.close()


@pytest.mark.asyncio
async def test_source_rate_limiting(tmp_path: Path) -> None:
    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)

    tenant_id = "test_rate_limit"
    # Set rate limit to 60 seconds
    tenant = TenantConfig.model_validate(
        {
            "tenant_id": tenant_id,
            "display_name": "Rate Limit Test",
            "sources": [
                {
                    "type": "local_fixture",
                    "path": fixture_path.as_posix(),
                    "rate_limit_min_interval_seconds": 60,
                }
            ],
            "store_backend": "sqlite",
            "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
            "job_backend": "sqlite",
            "search_backend": "sqlite",
        }
    )

    runner = TenantRunner.from_tenants([tenant])
    runtime = runner.get_runtime(tenant_id)
    sid = source_spec_identifier(runtime.base_sources[0])

    # Run 1: Success
    summary1 = await runner.run_tenant(tenant_id)
    assert sid in summary1.by_source_id

    # Run 2: Immediate follow up -> should be rate limited (skipped)
    summary2 = await runner.run_tenant(tenant_id)
    assert sid not in summary2.by_source_id

    # Manually backdate the last run to 61 seconds ago
    health = await runtime.store.get_source_health(sid)
    # Ensure TZ-aware
    health.last_run_at = (datetime.now(UTC) - timedelta(seconds=61)).isoformat()
    await runtime.store.save_source_health(sid, health)

    # Run 3: Now it should run again
    summary3 = await runner.run_tenant(tenant_id)
    assert sid in summary3.by_source_id

    await runner.close()


@pytest.mark.asyncio
async def test_source_health_thresholds_from_settings() -> None:
    # Test that changing settings.source_health_failure_streak_pause affects outcome
    settings = Settings()
    settings.source_health_failure_streak_pause = 5

    sid = "test:src"
    stats = SourceRunStats(fetched=0, emitted=0, failed=1)  # Failed run

    # After 4 failures, it should NOT be paused yet with streak=5
    previous = SourceHealth(
        source_id=sid,
        source_kind="test",
        source_name="src",
        last_run_at=datetime.now(UTC).isoformat(),
        last_success_at=None,
        failure_streak=4,
        success_count=0,
        last_fetched=0,
        last_emitted=0,
        last_failed=1,
        last_quarantined=0,
        baseline_emitted=0.0,
        drift_ratio=None,
        degraded=False,
        status="healthy",
        paused=False,
    )

    new_health = _update_source_health_payload(
        previous,
        source_id=sid,
        source_kind="test",
        source_name="src",
        stats=stats,
        finished_at=datetime.now(UTC),
        failure_streak_pause=settings.source_health_failure_streak_pause,
    )

    assert new_health.failure_streak == 5
    assert new_health.paused is True
    assert new_health.status == "paused"


@pytest.mark.asyncio
async def test_source_health_marks_transient_failures_as_failing() -> None:
    stats = SourceRunStats(fetched=0, emitted=0, failed=1)

    new_health = _update_source_health_payload(
        None,
        source_id="test:src",
        source_kind="test",
        source_name="src",
        stats=stats,
        finished_at=datetime.now(UTC),
        failure_streak_pause=3,
    )

    assert new_health.failure_streak == 1
    assert new_health.paused is False
    assert new_health.status == "failing"


@pytest.mark.asyncio
async def test_source_health_clears_stale_error_on_non_source_fetch_failure() -> None:
    previous = SourceHealth(
        source_id="test:src",
        source_kind="test",
        source_name="src",
        last_run_at="2026-06-12T00:00:00+00:00",
        last_success_at="2026-06-12T00:00:00+00:00",
        failure_streak=0,
        success_count=2,
        last_fetched=3,
        last_emitted=2,
        last_failed=0,
        last_quarantined=0,
        baseline_emitted=2.0,
        drift_ratio=1.0,
        degraded=False,
        status="healthy",
        last_error="403 Forbidden",
        last_error_at="2026-06-12T00:00:00+00:00",
        last_error_kind="source_fetch_failed",
    )
    stats = SourceRunStats(fetched=0, emitted=0, failed=1)

    new_health = _update_source_health_payload(
        previous,
        source_id="test:src",
        source_kind="test",
        source_name="src",
        stats=stats,
        finished_at=datetime.now(UTC),
        failure_streak_pause=3,
    )

    assert new_health.failure_streak == 1
    assert new_health.status == "failing"
    assert new_health.last_error is None
    assert new_health.last_error_at is None
    assert new_health.last_error_kind is None
