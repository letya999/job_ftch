"""Tests for the shared HTTP retry helper."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(status: int, headers: dict | None = None) -> httpx.Response:
    req = httpx.Request("GET", "https://example.com/job")
    resp = httpx.Response(status, headers=headers or {}, request=req)
    return resp


async def _raise_transport() -> httpx.Response:
    raise httpx.TransportError("connection reset")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFetchWithRetry:
    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self) -> None:
        from job_ftch.infrastructure.sources.http_retry import fetch_with_retry

        ok = _make_response(200)
        client = MagicMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=ok)

        resp = await fetch_with_retry(client, "https://example.com/job")
        assert resp.status_code == 200
        assert client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_repeated_429_stops_after_one_same_route_retry(self) -> None:
        from job_ftch.infrastructure.sources.http_retry import fetch_with_retry

        too_many = _make_response(429)
        ok = _make_response(200)
        client = MagicMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=[too_many, too_many, ok])

        with patch("asyncio.sleep", new_callable=AsyncMock):
            resp = await fetch_with_retry(client, "https://example.com/job", max_attempts=3)

        assert resp.status_code == 429
        assert client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_respects_retry_after_header(self) -> None:
        from job_ftch.infrastructure.sources.http_retry import fetch_with_retry

        too_many = _make_response(429, headers={"Retry-After": "2"})
        ok = _make_response(200)
        client = MagicMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=[too_many, ok])

        sleep_calls: list[float] = []

        async def fake_sleep(s: float) -> None:
            sleep_calls.append(s)

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await fetch_with_retry(client, "https://example.com/job", max_attempts=3)

        # At least one sleep should be >= 2s (the Retry-After value)
        assert any(s >= 2.0 for s in sleep_calls), f"sleep_calls={sleep_calls}"

    @pytest.mark.asyncio
    async def test_exhaustion_returns_last_response(self) -> None:
        from job_ftch.infrastructure.sources.http_retry import fetch_with_retry

        server_err = _make_response(503)
        client = MagicMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=server_err)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            resp = await fetch_with_retry(client, "https://example.com/job", max_attempts=2)

        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_403_is_returned_to_route_policy_without_retry(self) -> None:
        """Protection evidence must reach route policy without a blind retry."""
        from job_ftch.infrastructure.sources.http_retry import fetch_with_retry

        forbidden = _make_response(403)
        ok = _make_response(200)
        client = MagicMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=[forbidden, ok])

        with patch("asyncio.sleep", new_callable=AsyncMock):
            resp = await fetch_with_retry(client, "https://example.com/job")

        assert resp.status_code == 403
        assert client.get.call_count == 1


class TestFetchWithRetryFallback:
    @pytest.mark.asyncio
    async def test_single_attempt_when_tenacity_absent(self) -> None:
        """When tenacity is not importable the helper falls back to one attempt."""
        import job_ftch.infrastructure.sources.http_retry as retry_mod

        original = retry_mod._TENACITY_AVAILABLE
        try:
            retry_mod._TENACITY_AVAILABLE = False

            ok = _make_response(200)
            client = MagicMock(spec=httpx.AsyncClient)
            client.get = AsyncMock(return_value=ok)

            with patch("asyncio.sleep", new_callable=AsyncMock):
                resp = await retry_mod.fetch_with_retry(client, "https://example.com/job")

            assert resp.status_code == 200
            assert client.get.call_count == 1
        finally:
            retry_mod._TENACITY_AVAILABLE = original
