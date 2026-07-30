from unittest.mock import patch

import httpx
import pytest

from job_ftch.infrastructure.bypass.managed import ManagedScraperBypass


@pytest.mark.asyncio
async def test_managed_scraper_scrapfly() -> None:
    bypass = ManagedScraperBypass("https://api.scrapfly.io/scrape", "scrapfly_secret", "scrapfly")
    client = await bypass.apply_http(httpx.AsyncClient())

    with patch.object(client._client, "get") as mock_get:
        await client.get("https://example.com/jobs")

        mock_get.assert_called_once()
        url, kwargs = mock_get.call_args[0][0], mock_get.call_args[1]

        assert url == "https://api.scrapfly.io/scrape"
        assert kwargs["params"]["url"] == "https://example.com/jobs"
        assert kwargs["params"]["key"] == "scrapfly_secret"
        assert kwargs["headers"]["scp-sdk"] == "python"


@pytest.mark.asyncio
async def test_managed_scraper_zenrows() -> None:
    bypass = ManagedScraperBypass("https://api.zenrows.com/v1/", "zenrows_secret", "zenrows")
    client = await bypass.apply_http(httpx.AsyncClient())

    with patch.object(client._client, "get") as mock_get:
        await client.get("https://example.com/jobs")

        mock_get.assert_called_once()
        url, kwargs = mock_get.call_args[0][0], mock_get.call_args[1]

        assert url == "https://api.zenrows.com/v1/"
        assert kwargs["params"]["url"] == "https://example.com/jobs"
        assert "apikey" in kwargs["params"]
        assert kwargs["params"]["apikey"] == "zenrows_secret"
        assert "authorization" not in kwargs["headers"]
        assert "Authorization" not in kwargs["headers"]
