from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from job_ftch.infrastructure.sources.monitors.ashby import can_handle, discover


class _Response:
    status_code = 200

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self.url = "https://api.ashbyhq.com/posting-api/job-board/example"

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


class _HtmlResponse(_Response):
    def __init__(self, text: str) -> None:
        super().__init__({"jobs": []})
        self.text = text


class _HtmlClient(_Client):
    def __init__(self, text: str) -> None:
        super().__init__({"jobs": []})
        self._text = text

    async def get(self, url: str, **kwargs: object) -> _HtmlResponse:
        self.calls.append((url, kwargs))
        return _HtmlResponse(self._text)


def _listing_payload() -> dict[str, object]:
    path = (
        Path(__file__).parents[3]
        / "fixtures"
        / "real_world"
        / "monitors"
        / "ashby"
        / "listing.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_ashby_monitor_reads_recorded_listing() -> None:
    client = _Client(_listing_payload())
    spec = SimpleNamespace(url="https://jobs.ashbyhq.com/example", monitor_config={})

    items = await discover(spec, client)

    assert len(items) == 1
    item = items[0]
    assert item.title == "Machine Learning Engineer"
    assert item.locations == ["Remote", "Berlin"]
    assert item.base_salary == {"currency": "USD", "min": 120000, "max": 160000, "unit": "year"}
    assert item.metadata == {"department": "Engineering", "id": "ashby-1"}


@pytest.mark.asyncio
async def test_ashby_monitor_can_handle_recorded_board() -> None:
    client = _Client(_listing_payload())

    result = await can_handle("https://jobs.ashbyhq.com/example", client)

    assert result == {"token": "example", "jobs": 1}


@pytest.mark.asyncio
async def test_ashby_monitor_does_not_guess_token_from_an_unrelated_domain() -> None:
    client = _HtmlClient("<html><body>Open roles at Bolt</body></html>")

    result = await can_handle("https://bolt.eu/en/careers/positions/", client)

    assert result is None
    assert [url for url, _ in client.calls] == ["https://bolt.eu/en/careers/positions/"]
