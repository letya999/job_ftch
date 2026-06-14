from unittest.mock import AsyncMock, patch

import pytest

from job_ftch.adapters.source_validator import check_url_reachable, validate_sources


@pytest.mark.asyncio
async def test_check_url_reachable_success():
    with patch("httpx.AsyncClient.head", new_callable=AsyncMock) as mock_head:
        mock_head.return_value.status_code = 200
        ok, reason = await check_url_reachable("https://ok.com")
        assert ok is True
        assert reason == ""


@pytest.mark.asyncio
async def test_check_url_reachable_404():
    with patch("httpx.AsyncClient.head", new_callable=AsyncMock) as mock_head:
        mock_head.return_value.status_code = 404
        ok, reason = await check_url_reachable("https://fail.com")
        assert ok is False
        assert "404" in reason


@pytest.mark.asyncio
async def test_check_url_reachable_exception():
    with (
        patch("httpx.AsyncClient.head", side_effect=Exception("network error")),
        patch("httpx.AsyncClient.get", side_effect=Exception("network error")),
    ):
        ok, reason = await check_url_reachable("https://error.com")
        assert ok is False
        assert "network error" in reason


@pytest.mark.asyncio
async def test_validate_sources_mixed():
    links = ["https://ok.com", "https://fail.com", "@channel"]

    async def mock_reachable(url, **kwargs):
        if "ok.com" in url:
            return True, ""
        return False, "404"

    with patch(
        "job_ftch.adapters.source_validator.check_url_reachable", side_effect=mock_reachable
    ):
        results = await validate_sources(links)
        assert results["https://ok.com"] == (True, "")
        assert results["https://fail.com"] == (False, "404")
        assert results["@channel"] == (True, "no_telegram_client")
