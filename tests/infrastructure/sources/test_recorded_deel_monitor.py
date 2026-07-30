"""Recorded API contract for the Deel job-board monitor."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from job_ftch.infrastructure.sources.monitors import deel


class _Response:
    status_code = 200

    def __init__(self, payload: object) -> None:
        self.payload = payload

    def json(self) -> object:
        return self.payload

    def raise_for_status(self) -> None:
        return None


class _Client:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses

    async def get(self, url: str, **_: object) -> _Response:
        return _Response(self.responses[url])


@pytest.mark.asyncio
async def test_recorded_deel_discovery_maps_rich_job_data() -> None:
    postings = json.loads(
        (
            Path(__file__).parents[3]
            / "fixtures"
            / "real_world"
            / "monitors"
            / "deel"
            / "postings.json"
        ).read_text(encoding="utf-8")
    )
    url = deel._POSTINGS.format(org_id="org-1", board_id="board-1")
    spec = SimpleNamespace(
        url="https://jobs.deel.com/example",
        monitor_config={"slug": "example", "org_id": "org-1", "board_id": "board-1"},
    )

    jobs = await deel.discover(spec, _Client({url: postings}))

    assert len(jobs) == 1
    assert str(jobs[0].url).endswith("/example/job-details/role-42/overview")
    assert jobs[0].locations == ["Remote"]
    assert jobs[0].base_salary == {"currency": "USD", "min": 120000, "max": 180000, "unit": "year"}
    assert jobs[0].metadata == {"team": "Engineering", "department": "Platform", "id": "role-42"}


@pytest.mark.asyncio
async def test_deel_can_handle_validates_slug_and_settings() -> None:
    settings_url = deel._SETTINGS.format(slug="example")
    client = _Client({settings_url: {"organizationId": "org", "jobBoard": {"id": "board"}}})

    assert await deel.can_handle("https://jobs.deel.com/example", client) == {
        "slug": "example",
        "org_id": "org",
        "board_id": "board",
    }
    assert await deel.can_handle("https://jobs.deel.com/login", client) is None
    assert await deel.can_handle("https://example.org/jobs", client) is None
