from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from job_ftch.adapters.telegram_bot.api import _tenant_health
from job_ftch.application.pipeline import RunSummary
from job_ftch.domain import SourceHealth


class _Store:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.state = {
            "bot_scheduler:last_attempt_at": (now - timedelta(seconds=20)).isoformat(),
            "bot_scheduler:last_success_at": (now - timedelta(seconds=10)).isoformat(),
            "bot_scheduler:last_error": "",
            "bot_scheduler:last_publish_success_at": (now - timedelta(seconds=5)).isoformat(),
            "bot_scheduler:last_publish_error": "rate limit",
            "bot_scheduler:last_publish_sent": "2",
            "bot_scheduler:pending_publish_since": "",
        }
        self.health = [
            SourceHealth(
                source_id="career_site:example",
                source_kind="career_site",
                source_name="example",
                last_run_at=now.isoformat(),
                last_success_at=now.isoformat(),
                failure_streak=1,
                success_count=2,
                last_fetched=3,
                last_emitted=0,
                last_failed=0,
                last_quarantined=0,
                baseline_emitted=4.0,
                drift_ratio=0.0,
                degraded=True,
                status="degraded",
            )
        ]

    async def get_run_state(self, key: str) -> str | None:
        return self.state.get(key)

    async def list_source_health(self) -> list[SourceHealth]:
        return self.health


class _Runner:
    def __init__(self) -> None:
        self.store = _Store()

    def get_runtime(self, tenant_id: str) -> SimpleNamespace:
        assert tenant_id == "ai_jobs"
        return SimpleNamespace(store=self.store)

    async def get_status(self, tenant_id: str) -> RunSummary:
        assert tenant_id == "ai_jobs"
        return RunSummary(
            tenant_id=tenant_id,
            source_run_id="run-123",
            finished_at=datetime.now(UTC),
            fetched=3,
            emitted=1,
        )


@pytest.mark.asyncio
async def test_tenant_health_reports_store_source_scheduler_and_publish_state() -> None:
    payload = await _tenant_health(_Runner(), "ai_jobs")  # type: ignore[arg-type]

    assert payload["status"] == "degraded"
    assert payload["store"]["ok"] is True
    assert payload["last_run"]["source_run_id"] == "run-123"
    assert payload["sources"]["bad_source_ids"] == ["career_site:example"]
    assert payload["scheduler"]["last_error"] is None
    assert payload["publish"]["last_error"] == "rate limit"
    assert payload["publish"]["last_sent"] == 2
