from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from job_ftch.application.pipeline import RunSummary, SourceRunStats
from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.config import Settings
from job_ftch.domain import TenantConfig, source_spec_identifier

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
async def test_eviction_streak_keeps_source_enabled_and_success_resets_it(tmp_path: Path) -> None:
    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)

    tenant_id = "test_eviction_pause"
    tenant = TenantConfig.model_validate(
        {
            "tenant_id": tenant_id,
            "display_name": "Eviction Test",
            "sources": [{"type": "local_fixture", "path": fixture_path.as_posix()}],
            "store_backend": "sqlite",
            "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
            "job_backend": "sqlite",
            "search_backend": "sqlite",
        }
    )
    settings = Settings()
    settings.source_eviction_pause_threshold = 2
    settings.source_health_probe_every_n_runs = 2

    runner = TenantRunner.from_tenants([tenant], base_settings=settings)
    runtime = runner.get_runtime(tenant_id)
    sid = source_spec_identifier(runtime.base_sources[0])

    for _ in range(2):
        summary = RunSummary(started_at=datetime.now(UTC), finished_at=datetime.now(UTC))
        summary.by_source_id[sid] = SourceRunStats()
        summary.source_evictions.append(
            {
                "source_id": sid,
                "source_kind": "local_fixture",
                "source_name": "fixture",
                "eviction_kind": "hard_deadline",
            }
        )
        await runner._update_source_health(runtime, summary)

    health = await runtime.store.get_source_health(sid)
    assert health is not None
    assert health.paused is False
    assert health.eviction_streak == 2
    assert health.last_eviction_kind == "hard_deadline"

    completed = await runner.run_tenant(tenant_id)
    assert sid in completed.by_source_id

    health_after_probe = await runtime.store.get_source_health(sid)
    assert health_after_probe is not None
    assert health_after_probe.paused is False
    assert health_after_probe.eviction_streak == 0

    await runner.close()
