from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient

from job_ftch.adapters.fastapi.adapter import create_app
from job_ftch.application.pipeline import RunSummary
from job_ftch.application.tenant_store import TenantStore
from job_ftch.config import Settings
from job_ftch.domain import JobRecord, ObservationLedgerEntry, RawItem, SourceKind, TenantInfo
from job_ftch.domain.observation import content_hash_for_raw_item
from job_ftch.infrastructure.stores.in_memory import InMemoryStore


class _Runner:
    def __init__(self) -> None:
        self.store = TenantStore("lane_a", InMemoryStore())
        self._tenants = [TenantInfo(tenant_id="lane_a", display_name="Lane A", source_count=1)]
        self.queued: list[dict[str, str]] = []
        self.jobs: list[object] = []
        self.runs: dict[str, RunSummary] = {}

    async def list_tenants(self) -> list[TenantInfo]:
        return self._tenants

    def get_runtime(self, tenant_id: str) -> SimpleNamespace:
        if tenant_id != "lane_a":
            raise KeyError(tenant_id)
        return SimpleNamespace(store=self.store)

    async def list_runs(self, *, tenant_id: str, limit: int) -> list[object]:
        del tenant_id
        return list(self.runs.values())[:limit]

    async def get_run(self, run_id: str, *, tenant_id: str) -> RunSummary | None:
        del tenant_id
        return self.runs.get(run_id)

    async def latest_jobs(
        self, tenant_id: str, *, limit: int, since: datetime | None = None
    ) -> list[object]:
        return []

    async def list_jobs(self, tenant_id: str, *, since: datetime | None = None) -> list[object]:
        del tenant_id, since
        return self.jobs

    async def run_tenant(self, tenant_id: str, *, run_id: str, trigger: str) -> None:
        self.queued.append({"tenant_id": tenant_id, "run_id": run_id, "trigger": trigger})


async def _seed_observations(runner: _Runner) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for index in range(2):
        raw = RawItem(
            source_kind=SourceKind.DEBUG,
            source_name="debug",
            external_id=str(index),
            text=f"job {index}",
        )
        await runner.store.record_observation(
            ObservationLedgerEntry(
                observation_id=f"lane_a:{index}",
                tenant_id="lane_a",
                stable_id=raw.stable_id,
                content_hash=content_hash_for_raw_item(raw),
                decision_version="test",
                raw_item=raw,
                observed_at=base + timedelta(minutes=index),
            )
        )


def test_private_tenant_api_requires_bearer_and_scopes_tenant() -> None:
    runner = _Runner()
    app = create_app(
        runner=runner,
        settings=Settings(api_token="secret", api_tenant_allowlist=["lane_a"]),
    )
    client = TestClient(app)

    assert client.get("/v1/tenants").status_code == 401
    assert (
        client.get(
            "/v1/tenants/lane_b/runs", headers={"Authorization": "Bearer secret"}
        ).status_code
        == 403
    )
    response = client.get("/v1/tenants", headers={"Authorization": "Bearer secret"})
    assert response.status_code == 200
    assert response.json()["tenants"][0]["tenant_id"] == "lane_a"


def test_observation_cursor_is_stable() -> None:
    runner = _Runner()
    import asyncio

    asyncio.run(_seed_observations(runner))
    app = create_app(runner=runner, settings=Settings(api_token="secret"))
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret"}

    first = client.get("/v1/tenants/lane_a/observations?limit=1", headers=headers)
    assert first.status_code == 200
    body = first.json()
    assert len(body["items"]) == 1
    assert body["next_cursor"]
    second = client.get(
        f"/v1/tenants/lane_a/observations?limit=1&cursor={body['next_cursor']}",
        headers=headers,
    )
    assert second.status_code == 200
    assert second.json()["items"][0]["observation_id"] != body["items"][0]["observation_id"]


def test_run_endpoint_returns_stable_run_id() -> None:
    runner = _Runner()
    app = create_app(runner=runner, settings=Settings(api_token="secret"))
    client = TestClient(app)

    response = client.post(
        "/v1/tenants/lane_a/runs",
        headers={"Authorization": "Bearer secret"},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["run_id"]
    assert runner.queued == [{"tenant_id": "lane_a", "run_id": body["run_id"], "trigger": "api"}]


def test_outcome_and_job_cursors_are_stable() -> None:
    runner = _Runner()
    import asyncio

    async def seed() -> None:
        await runner.store.record_operational_outcome(
            "review",
            {"source_run_id": "run-1", "recorded_at": "2026-01-01T00:00:00+00:00"},
        )
        await runner.store.record_operational_outcome(
            "review",
            {"source_run_id": "run-1", "recorded_at": "2026-01-01T00:00:01+00:00"},
        )

    asyncio.run(seed())
    runner.jobs = [
        JobRecord(
            raw_item_id="job-1",
            source_kind=SourceKind.DEBUG,
            source_name="debug",
            title="Engineer 1",
            fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        JobRecord(
            raw_item_id="job-2",
            source_kind=SourceKind.DEBUG,
            source_name="debug",
            title="Engineer 2",
            fetched_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
        ),
    ]
    app = create_app(runner=runner, settings=Settings(api_token="secret"))
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret"}

    first = client.get("/v1/tenants/lane_a/outcomes?limit=1", headers=headers)
    assert first.status_code == 200
    body = first.json()
    assert len(body["items"]) == 1
    assert body["next_cursor"]
    second = client.get(
        f"/v1/tenants/lane_a/outcomes?limit=1&cursor={body['next_cursor']}",
        headers=headers,
    )
    assert second.status_code == 200
    assert second.json()["items"][0]["outcome_id"] != body["items"][0]["outcome_id"]

    first_jobs = client.get("/v1/tenants/lane_a/jobs?limit=1", headers=headers)
    assert first_jobs.status_code == 200
    jobs_body = first_jobs.json()
    assert jobs_body["next_cursor"]
    second_jobs = client.get(
        f"/v1/tenants/lane_a/jobs?limit=1&cursor={jobs_body['next_cursor']}",
        headers=headers,
    )
    assert second_jobs.status_code == 200
    assert second_jobs.json()["items"][0]["job_id"] != jobs_body["items"][0]["job_id"]


def test_run_items_join_accepted_review_and_rejected_statuses() -> None:
    runner = _Runner()
    import asyncio

    run_id = "run-1"
    summary = RunSummary(
        tenant_id="lane_a",
        source_run_id=run_id,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        finished_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        fetched=3,
        emitted=1,
    )
    summary.source_identity_stats("debug", "debug")
    runner.runs[run_id] = summary
    runner.jobs = [
        JobRecord(
            raw_item_id="accepted-1",
            source_kind=SourceKind.DEBUG,
            source_name="debug",
            title="Accepted",
            canonical_url="https://example.test/jobs/accepted-1",
            fetched_at=datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC),
            metadata={"source_run_id": run_id, "original_posting_text": "RAW POSTING BODY"},
        )
    ]

    async def seed() -> None:
        await runner.store.record_operational_outcome(
            "review",
            {
                "source_run_id": run_id,
                "source_kind": "debug",
                "source_name": "debug",
                "recorded_at": "2026-01-01T00:00:01+00:00",
                "stable_id": "review-1",
            },
        )
        await runner.store.record_operational_outcome(
            "rejected",
            {
                "source_run_id": run_id,
                "source_kind": "debug",
                "source_name": "debug",
                "recorded_at": "2026-01-01T00:00:03+00:00",
                "outcome": "DROPPED",
                "stable_id": "rejected-1",
            },
        )

    asyncio.run(seed())
    app = create_app(runner=runner, settings=Settings(api_token="secret"))
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret"}

    response = client.get(
        f"/v1/tenants/lane_a/runs/{run_id}/items?status=accepted,rejected&limit=10",
        headers=headers,
    )
    assert response.status_code == 200
    assert [item["status"] for item in response.json()["items"]] == ["accepted", "rejected"]

    review = client.get(
        f"/v1/tenants/lane_a/runs/{run_id}/items?status=review",
        headers=headers,
    )
    assert review.status_code == 200
    assert review.json()["items"][0]["item"]["stable_id"] == "review-1"

    detail = client.get(
        "/v1/tenants/lane_a/jobs/" + runner.jobs[0].job_id,
        headers=headers,
    )
    assert detail.status_code == 200
    assert detail.json()["raw_body"] == "RAW POSTING BODY"
    assert detail.json()["job"]["canonical_url"] == "https://example.test/jobs/accepted-1"

    stats = client.get(f"/v1/tenants/lane_a/runs/{run_id}/stats", headers=headers)
    assert stats.status_code == 200
    assert stats.json()["counters"]["fetched"] == 3
