from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from unittest.mock import AsyncMock

import httpx
import pytest

from job_ftch.infrastructure.sources import http_retry
from job_ftch.infrastructure.sources.http_retry import fetch_with_retry, parse_retry_after
from job_ftch.infrastructure.sources.source_deadline import source_deadline_scope


def test_retry_after_parses_delta_seconds_and_caps() -> None:
    assert parse_retry_after("12") == 12.0
    assert parse_retry_after("999", cap_seconds=30) == 30.0
    assert parse_retry_after("-2") == 0.0


def test_retry_after_parses_http_date() -> None:
    now = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    value = format_datetime(now + timedelta(seconds=15), usegmt=True)
    assert parse_retry_after(value, now=now) == pytest.approx(15.0)


def test_retry_after_rejects_invalid_and_non_finite_values() -> None:
    assert parse_retry_after("not-a-date") is None
    assert parse_retry_after("nan") is None
    assert parse_retry_after(None) is None


@pytest.mark.asyncio
async def test_post_is_not_replayed_without_explicit_safety(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("POST", "https://example.test/api")
    response = httpx.Response(503, request=request)
    client = AsyncMock()
    client.post.return_value = response
    monkeypatch.setattr(http_retry, "_TENACITY_AVAILABLE", True)

    result = await fetch_with_retry(client, str(request.url), method="POST", max_attempts=3)

    assert result is response
    client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_server_status_has_at_most_one_same_route_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("GET", "https://example.test/jobs")
    client = AsyncMock()
    client.get.side_effect = [
        httpx.Response(503, request=request),
        httpx.Response(503, request=request),
    ]
    monkeypatch.setattr(http_retry, "_TENACITY_AVAILABLE", True)
    monkeypatch.setattr(http_retry, "exponential_backoff_s", lambda *args, **kwargs: 0.0)

    result = await fetch_with_retry(client, str(request.url), max_attempts=6)

    assert result.status_code == 503
    assert client.get.await_count == 2


@pytest.mark.asyncio
async def test_transport_retry_amplification_has_finite_upper_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AsyncMock()
    client.get.side_effect = httpx.ConnectError("offline")
    monkeypatch.setattr(http_retry, "_TENACITY_AVAILABLE", True)
    monkeypatch.setattr(http_retry, "exponential_backoff_s", lambda *args, **kwargs: 0.0)

    with pytest.raises(http_retry.RetryError):
        await fetch_with_retry(
            client,
            "https://example.test/jobs?token=must-not-be-logged",
            max_attempts=99,
        )

    assert client.get.await_count == 2


@pytest.mark.asyncio
async def test_retry_wait_uses_twenty_percent_of_remaining_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("GET", "https://example.test/jobs")
    client = AsyncMock()
    client.get.side_effect = [
        httpx.Response(429, headers={"Retry-After": "30"}, request=request),
        httpx.Response(200, request=request),
    ]
    sleeps: list[float] = []
    monkeypatch.setattr(http_retry, "_TENACITY_AVAILABLE", True)

    async def _capture_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(http_retry, "sleep_with_source_deadline", _capture_sleep)
    async with source_deadline_scope(asyncio.get_running_loop().time() + 10.0):
        await fetch_with_retry(client, str(request.url))

    assert sleeps == [pytest.approx(2.0, abs=0.1)]
