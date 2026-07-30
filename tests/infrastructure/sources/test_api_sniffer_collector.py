from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from job_ftch.infrastructure.sources.monitors.api_sniffer import BoundedResponseCollector


class _Response:
    def __init__(
        self,
        data: object,
        *,
        url: str = "https://example.test/api/jobs",
        method: str = "GET",
        request_headers: dict[str, str] | None = None,
        post_data: str | None = None,
        delay: float = 0.0,
    ) -> None:
        self.url = url
        self._raw = json.dumps(data).encode()
        self._delay = delay
        self.request = SimpleNamespace(
            method=method,
            headers=request_headers or {},
            post_data=post_data,
        )

    async def all_headers(self) -> dict[str, str]:
        return {
            "content-type": "application/json",
            "content-length": str(len(self._raw)),
        }

    async def body(self) -> bytes:
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._raw


def _collector(**overrides: int) -> BoundedResponseCollector:
    values = {
        "max_responses": 5,
        "max_single_bytes": 1024,
        "max_total_bytes": 2048,
        "decode_concurrency": 2,
    }
    values.update(overrides)
    return BoundedResponseCollector(**values)


@pytest.mark.asyncio
async def test_collector_bounds_response_count_and_owns_tasks() -> None:
    collector = _collector(max_responses=2)
    for index in range(5):
        collector.schedule(_Response({"id": index}, delay=0.001))
    await collector.drain()
    assert len(collector.payloads) <= 2
    assert collector.truncated
    assert not collector._tasks


@pytest.mark.asyncio
async def test_completed_responses_still_consume_total_capture_count() -> None:
    collector = _collector(max_responses=2)
    for index in range(3):
        collector.schedule(_Response({"id": index}))
        await collector.drain()
    assert collector.scheduled_count == 2
    assert len(collector.payloads) == 2
    assert collector.truncated


@pytest.mark.asyncio
async def test_collector_rejects_single_and_total_oversize_payloads() -> None:
    single = _collector(max_single_bytes=20)
    single.schedule(_Response({"description": "x" * 100}))
    await single.drain()
    assert single.payloads == []
    assert single.truncated

    total = _collector(max_total_bytes=35)
    total.schedule(_Response({"id": 1, "value": "abcdefgh"}))
    await total.drain()
    total.schedule(_Response({"id": 2, "value": "abcdefgh"}))
    await total.drain()
    assert len(total.payloads) == 1
    assert total.truncated


@pytest.mark.asyncio
async def test_concurrent_decodes_cannot_race_past_total_memory_cap() -> None:
    response = _Response({"description": "x" * 40}, delay=0.001)
    collector = _collector(
        max_responses=2,
        max_single_bytes=len(response._raw) + 1,
        max_total_bytes=len(response._raw) + 1,
        decode_concurrency=2,
    )
    collector.schedule(response)
    collector.schedule(_Response({"description": "x" * 40}, delay=0.001))
    await collector.drain()

    assert len(collector.payloads) == 1
    assert collector.total_bytes <= collector.max_total_bytes
    assert collector.truncated


@pytest.mark.asyncio
async def test_collector_preserves_safe_replay_shape_without_secrets() -> None:
    collector = _collector()
    collector.schedule(
        _Response(
            {"jobs": []},
            method="POST",
            request_headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer secret",
                "Cookie": "session=secret",
                "X-Requested-With": "fetch",
            },
            post_data='{"page": 1}',
        )
    )
    await collector.drain()
    captured = collector.payloads[0]
    assert captured.method == "POST"
    assert captured.post_data == '{"page": 1}'
    assert captured.request_headers == {
        "content-type": "application/json",
        "x-requested-with": "fetch",
    }
    assert captured.replay_cookie_header == "session=secret"
    assert "session=secret" not in repr(captured)


@pytest.mark.asyncio
async def test_collector_cancels_and_joins_pending_decodes() -> None:
    collector = _collector()
    collector.schedule(_Response({"jobs": []}, delay=10.0))
    await asyncio.sleep(0)
    await collector.cancel()
    assert not collector._tasks
