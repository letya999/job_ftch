from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from job_ftch.infrastructure.sources.monitors.lever import can_handle, discover


class _Response:
    status_code = 200

    def __init__(self, payload: list[dict[str, object]]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> list[dict[str, object]]:
        return self._payload


class _Client:
    def __init__(self, payload: list[dict[str, object]]) -> None:
        self._payload = payload
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def get(self, url: str, **kwargs: object) -> _Response:
        self.calls.append((url, kwargs))
        return _Response(self._payload)


def _payload() -> list[dict[str, object]]:
    path = (
        Path(__file__).parents[3]
        / "fixtures"
        / "real_world"
        / "monitors"
        / "lever"
        / "listing.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_lever_monitor_reads_recorded_listing() -> None:
    client = _Client(_payload())
    spec = SimpleNamespace(url="https://jobs.lever.co/example", monitor_config={})

    items = await discover(spec, client)

    assert len(items) == 1
    item = items[0]
    assert item.title == "Platform Engineer"
    assert item.locations == ["Remote"]
    assert item.base_salary == {"currency": "USD", "min": 130000, "max": 170000, "unit": "year"}
    assert item.metadata == {"team": "Engineering", "department": "Platform", "id": "lever-1"}
    assert "Requirements" in (item.description or "")


@pytest.mark.asyncio
async def test_lever_monitor_can_handle_recorded_board() -> None:
    result = await can_handle("https://jobs.lever.co/example", _Client(_payload()))

    assert result == {"token": "example", "jobs": 1}
