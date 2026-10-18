"""Public catalog reads a bounded, compact projection from PostgreSQL."""

import json
from unittest.mock import AsyncMock

import pytest

from job_ftch.infrastructure.backends.jobs.postgres import PostgreSQLJobBackend


@pytest.mark.asyncio
async def test_public_projection_avoids_full_group_json(monkeypatch: pytest.MonkeyPatch) -> None:
    class Connection:
        query = ""
        limit = 0

        def acquire(self) -> "Connection":
            return self

        async def __aenter__(self) -> "Connection":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def fetch(self, query: str, limit: int) -> list[dict[str, str]]:
            self.query, self.limit = query, limit
            return [
                {
                    "public_job": json.dumps(
                        {
                            "group_id": "group-1",
                            "raw_item_id": "raw-1",
                            "source_kind": "career_site",
                            "source_name": "example",
                            "title": "Engineer",
                            "company": "Acme",
                            "description": "Vacancy",
                            "location": "Москва",
                            "work_mode": "remote",
                            "seniority": "senior",
                            "tools_stack": ["python"],
                            "posted_at": "2026-10-01T10:00:00+00:00",
                            "compensation": None,
                            "canonical_url": "https://example.org/jobs/1",
                        }
                    )
                }
            ]

    conn = Connection()
    backend = object.__new__(PostgreSQLJobBackend)
    monkeypatch.setattr(backend, "_get_pool", AsyncMock(return_value=conn))

    jobs = await backend.list_public_canonical_jobs(1000)

    assert len(jobs) == 1
    assert jobs[0].group_id == "group-1"
    assert jobs[0].title == "Engineer"
    assert jobs[0].company == "Acme"
    assert jobs[0].location == "Москва"
    assert jobs[0].work_mode.value == "remote"
    assert jobs[0].tools_stack == ("python",)
    assert str(jobs[0].canonical_url) == "https://example.org/jobs/1"
    assert conn.limit == 1000
    assert "jsonb_build_object" in conn.query
    assert "left(raw_json->'canonical_job'->>'description', 360)" in conn.query
    for field in (
        "company",
        "location",
        "city",
        "region",
        "country",
        "work_mode",
        "seniority",
        "tools_stack",
        "skills_explicit",
        "posted_at",
        "compensation",
        "canonical_url",
    ):
        assert f"'{field}'" in conn.query
    assert "SELECT raw_json FROM jf_job_groups" not in conn.query
