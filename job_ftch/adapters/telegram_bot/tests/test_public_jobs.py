from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from job_ftch.adapters.telegram_bot.public_jobs import build_public_jobs_response


class Store:
    async def get_run_state(self, key: str) -> str | None:
        if key.endswith("sent_ids"):
            return json.dumps(["group-published"])
        if key.endswith("sent_urls"):
            return json.dumps([])
        return None


class Runner:
    def __init__(self) -> None:
        self.store = Store()

    def get_runtime(self, tenant_id: str) -> SimpleNamespace:
        assert tenant_id == "ai_jobs"
        return SimpleNamespace(store=self.store)

    async def latest_jobs(self, tenant_id: str, *, limit: int) -> list[SimpleNamespace]:
        assert tenant_id == "ai_jobs"
        assert limit >= 100
        return [
            SimpleNamespace(
                group_id="group-published",
                title="Published ML Engineer",
                company="Example AI",
                description="A public vacancy.",
                location="Remote",
                work_mode="remote",
                seniority="senior",
                tools_stack=("Python",),
                skills_explicit=(),
                source_name="ai_engineer_jobs",
                source_kind="telegram_channel",
                posted_at=None,
                canonical_url="https://example.com/jobs/1",
            ),
            SimpleNamespace(
                group_id="group-private",
                title="Not published",
                company="Private AI",
                description="Do not expose.",
                location=None,
                work_mode="unknown",
                seniority="unknown",
                tools_stack=(),
                skills_explicit=(),
                source_name="private",
                source_kind="telegram_channel",
                posted_at=None,
                canonical_url="https://example.com/jobs/2",
            ),
        ]


@pytest.mark.asyncio
async def test_public_jobs_returns_postgres_catalog_items() -> None:
    payload = await build_public_jobs_response(Runner(), "ai_jobs", limit=20)
    assert payload["job_count"] == 2
    assert payload["jobs"][0]["title"] == "Published ML Engineer"


@pytest.mark.asyncio
async def test_public_jobs_rejects_non_public_tenant() -> None:
    with pytest.raises(LookupError):
        await build_public_jobs_response(Runner(), "private")
