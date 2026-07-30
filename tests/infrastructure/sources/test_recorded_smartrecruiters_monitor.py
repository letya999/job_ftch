from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from job_ftch.infrastructure.sources.monitors.smartrecruiters import (
    _api_list_url,
    _has_smartrecruiters_signal,
    _posting_url,
    _token_from_url,
    can_handle,
    discover,
)


class _Response:
    status_code = 200

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self.url = "https://api.smartrecruiters.com/v1/companies/example/postings"
        self.text = ""

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


class _Client:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def get(self, url: str, **kwargs: object) -> _Response:
        self.calls.append((url, kwargs))
        return _Response(self._payload)


class _HtmlThenApiClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self._calls = 0

    async def get(self, url: str, **kwargs: object) -> _Response:
        del url, kwargs
        self._calls += 1
        response = _Response(self._payload)
        if self._calls == 1:
            response.url = "https://careers.example.com"
            response.text = '<a href="https://jobs.smartrecruiters.com/example">Jobs</a>'
        return response


def _payload() -> dict[str, object]:
    path = (
        Path(__file__).parents[3]
        / "fixtures"
        / "real_world"
        / "monitors"
        / "smartrecruiters"
        / "listing.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_smartrecruiters_url_helpers() -> None:
    assert _token_from_url("https://jobs.smartrecruiters.com/example") == "example"
    assert _token_from_url("https://jobs.smartrecruiters.com/api") is None
    assert _api_list_url("example").endswith("/companies/example/postings")
    assert _posting_url("example", "42").endswith("/example/42")
    assert _has_smartrecruiters_signal("https://careers.example.com", "smartrecruiters.com")


@pytest.mark.asyncio
async def test_smartrecruiters_monitor_reads_recorded_listing() -> None:
    client = _Client(_payload())
    spec = SimpleNamespace(url="https://jobs.smartrecruiters.com/example", monitor_config={})

    urls = await discover(spec, client)

    assert urls == {
        "https://jobs.smartrecruiters.com/example/smart-101",
        "https://jobs.smartrecruiters.com/example/smart-102",
    }
    assert client.calls[0][1]["params"] == {"limit": 100, "offset": 0}


@pytest.mark.asyncio
async def test_smartrecruiters_monitor_can_handle_recorded_board() -> None:
    result = await can_handle("https://jobs.smartrecruiters.com/example", _Client(_payload()))

    assert result == {"token": "example", "jobs": 2}


@pytest.mark.asyncio
async def test_smartrecruiters_monitor_detects_embedded_recorded_signal() -> None:
    result = await can_handle("https://careers.example.com", _HtmlThenApiClient(_payload()))

    assert result == {"token": "example", "jobs": 2}
