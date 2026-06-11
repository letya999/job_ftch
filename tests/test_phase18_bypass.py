from unittest.mock import MagicMock

import httpx
import pytest

from job_ftch.infrastructure.bypass.managed import ManagedScraperBypass
from job_ftch.infrastructure.bypass.noop import NoopBypass
from job_ftch.infrastructure.bypass.proxy_rotator import ProxyRotatorBypass
from job_ftch.infrastructure.bypass.stealth_browser import StealthBrowserBypass


@pytest.mark.asyncio
async def test_noop_bypass_returns_client_unchanged():
    client = httpx.AsyncClient()
    bypass = NoopBypass()
    assert await bypass.apply_http(client) is client


@pytest.mark.asyncio
async def test_proxy_rotator_cycles_proxies():
    proxies = ["http://proxy1.com", "http://proxy2.com"]
    bypass = ProxyRotatorBypass(proxies)
    client = httpx.AsyncClient()

    client1 = await bypass.apply_http(client)
    assert client1 is not client

    client2 = await bypass.apply_http(client)
    assert client2 is not client1


@pytest.mark.asyncio
async def test_proxy_rotator_empty_list_is_noop():
    bypass = ProxyRotatorBypass([])
    client = httpx.AsyncClient()
    assert await bypass.apply_http(client) is client


@pytest.mark.asyncio
async def test_managed_scraper_bypass_scrapfly_sets_headers():
    bypass = ManagedScraperBypass(
        api_url="https://api.scrapfly.io", api_key="test_key", provider="scrapfly"
    )
    client = httpx.AsyncClient(headers={"X-Test": "Value"})
    configured = await bypass.apply_http(client)

    assert str(configured.base_url).rstrip("/") == "https://api.scrapfly.io"
    assert configured.params["key"] == "test_key"
    assert configured.headers["scp-sdk"] == "python"
    assert configured.headers["X-Test"] == "Value"


@pytest.mark.asyncio
async def test_stealth_browser_raises_without_dep(monkeypatch):
    import job_ftch.infrastructure.bypass.stealth_browser as stealth_browser

    monkeypatch.setattr(stealth_browser, "_STEALTH_AVAILABLE", False)

    bypass = StealthBrowserBypass()
    mock_page = MagicMock()

    with pytest.raises(ImportError, match="playwright-stealth is not installed"):
        await bypass.apply_page(mock_page)
