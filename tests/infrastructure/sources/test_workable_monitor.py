from __future__ import annotations

import pytest

from job_ftch.infrastructure.sources.monitors.workable import can_handle


class _Response:
    status_code = 200
    text = "<html><body>Open roles at Bolt</body></html>"

    def json(self) -> dict[str, object]:
        return {"total": 0}

    def raise_for_status(self) -> None:
        return None


class _Client:
    def __init__(self) -> None:
        self.posts: list[str] = []

    async def get(self, url: str, **kwargs: object) -> _Response:
        del url, kwargs
        return _Response()

    async def post(self, url: str, **kwargs: object) -> _Response:
        del kwargs
        self.posts.append(url)
        return _Response()


@pytest.mark.asyncio
async def test_workable_monitor_does_not_guess_a_slug_from_unrelated_domain() -> None:
    client = _Client()

    result = await can_handle("https://bolt.eu/en/careers/positions/", client)

    assert result is None
    assert client.posts == []
