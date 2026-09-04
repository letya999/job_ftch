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
                quality_important=True,
            )
        ]

    async def get_run_state(self, key: str) -> str | None:
        return self.state.get(key)

    async def list_source_health(self) -> list[SourceHealth]:
        return self.health


class _Runner:
    def __init__(self) -> None:
        self.store = _Store()
        self.catalog_ids = ["career_site:example"]

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

    async def list_sources(self, tenant_id: str) -> list[dict[str, str]]:
        assert tenant_id == "ai_jobs"
        return [{"source_id": source_id} for source_id in self.catalog_ids]

    async def set_source_important(
        self,
        tenant_id: str,
        source_id: str,
        *,
        important: bool,
        set_by: str = "operator",
        note: str | None = None,
    ) -> dict[str, object]:
        assert tenant_id == "ai_jobs"
        for h in self.store.health:
            if h.source_id == source_id or h.source_name == source_id:
                self.store.health = [h.model_copy(update={"quality_important": important})]
        return await self.list_source_quality(tenant_id)

    async def list_source_quality(self, tenant_id: str) -> dict[str, object]:
        assert tenant_id == "ai_jobs"
        return {
            "tenant_id": tenant_id,
            "window_runs": 20,
            "important": [
                {"source_key": h.source_name, "source_id": h.source_id}
                for h in self.store.health
                if h.quality_important
            ],
            "reliable": [],
            "rich": [],
            "high_relevance": [],
        }


@pytest.mark.asyncio
async def test_tenant_health_reports_store_source_scheduler_and_publish_state() -> None:
    payload = await _tenant_health(_Runner(), "ai_jobs")  # type: ignore[arg-type]

    assert payload["status"] == "degraded"
    assert payload["store"]["ok"] is True
    assert payload["last_run"]["source_run_id"] == "run-123"
    assert payload["sources"]["bad_source_ids"] == ["career_site:example"]
    assert payload["sources"]["important"] == 1
    assert payload["sources"]["watch_source_ids"] == ["career_site:example"]
    assert payload["scheduler"]["last_error"] is None
    assert payload["publish"]["last_error"] == "rate limit"
    assert payload["publish"]["last_sent"] == 2


@pytest.mark.asyncio
async def test_tenant_health_ignores_stale_search_overlay_rows() -> None:
    runner = _Runner()
    now = datetime.now(UTC)
    runner.store.health.append(
        SourceHealth(
            source_id="career_site:hirehi_ru_kw1",
            source_kind="career_site",
            source_name="hirehi_ru_kw1",
            last_run_at=(now - timedelta(days=12)).isoformat(),
            last_success_at=(now - timedelta(days=12)).isoformat(),
            failure_streak=0,
            success_count=4,
            last_fetched=0,
            last_emitted=0,
            last_failed=0,
            last_quarantined=0,
            baseline_emitted=5.0,
            drift_ratio=0.0,
            degraded=True,
            status="degraded",
        )
    )

    payload = await _tenant_health(runner, "ai_jobs")  # type: ignore[arg-type]

    assert payload["sources"]["total"] == 1
    assert payload["sources"]["bad_source_ids"] == ["career_site:example"]


def test_api_pipeline_sources_important_and_quality(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.testclient import TestClient

    from job_ftch.adapters.telegram_bot.api import create_app

    monkeypatch.setenv("JOB_FTCH_AUTH_TELEGRAM_BOT_TOKEN", "123456:test-token")
    monkeypatch.setenv("JOB_FTCH_AUTH_TELEGRAM_BOT_SECRET_TOKEN", "secret-token")
    monkeypatch.setenv("JOB_FTCH_AUTH_TELEGRAM_BOT_BRIDGE_API_KEY", "test-bridge-key")

    runner = _Runner()
    app = create_app(runner=runner)  # type: ignore[arg-type]
    client = TestClient(app)
    headers = {"x-api-key": "test-bridge-key"}

    # 1. 403 when missing or wrong API key
    res = client.post(
        "/pipeline/sources/ai_jobs/important",
        json={"source_id": "career_site:example"},
        headers={"x-api-key": "bad"},
    )
    assert res.status_code == 403

    # 2. 400 when missing source_id
    res = client.post(
        "/pipeline/sources/ai_jobs/important",
        json={"important": True},
        headers=headers,
    )
    assert res.status_code == 400
    assert "source_id is required" in res.text

    # 3. 400 when important is not a boolean
    res = client.post(
        "/pipeline/sources/ai_jobs/important",
        json={"source_id": "career_site:example", "important": "not-a-bool"},
        headers=headers,
    )
    assert res.status_code == 400
    assert "important must be a boolean" in res.text

    # 4. POST .../important pin
    res = client.post(
        "/pipeline/sources/ai_jobs/important",
        json={"source_id": "career_site:example", "important": True, "note": "key board"},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert any(item["source_id"] == "career_site:example" for item in body["important"])

    # 5. GET .../quality
    res = client.get("/pipeline/sources/ai_jobs/quality", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert any(item["source_id"] == "career_site:example" for item in body["important"])

    # 6. POST .../important unpin
    res = client.post(
        "/pipeline/sources/ai_jobs/important",
        json={"source_id": "career_site:example", "important": False},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert not any(item["source_id"] == "career_site:example" for item in body["important"])
