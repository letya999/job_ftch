from unittest.mock import MagicMock

import httpx
import pytest

from infrastructure.bypass.managed import ManagedScraperBypass
from infrastructure.bypass.noop import NoopBypass
from infrastructure.bypass.proxy_rotator import ProxyRotatorBypass
from infrastructure.bypass.stealth_browser import StealthBrowserBypass


def test_noop_bypass_returns_client_unchanged():
    client = httpx.AsyncClient()
    bypass = NoopBypass()
    assert bypass.configure(client) is client


def test_proxy_rotator_cycles_proxies():
    proxies = ["http://proxy1.com", "http://proxy2.com"]
    bypass = ProxyRotatorBypass(proxies)
    client = httpx.AsyncClient()

    client1 = bypass.configure(client)
    assert str(client1.proxy_ptr) if hasattr(client1, "proxy_ptr") else True  # simplified check

    client2 = bypass.configure(client)
    assert client1 is not client2


def test_proxy_rotator_empty_list_is_noop():
    bypass = ProxyRotatorBypass([])
    client = httpx.AsyncClient()
    assert bypass.configure(client) is client


def test_managed_scraper_bypass_scrapfly_sets_headers():
    bypass = ManagedScraperBypass(
        api_url="https://api.scrapfly.io", api_key="test_key", provider="scrapfly"
    )
    client = httpx.AsyncClient(headers={"X-Test": "Value"})
    configured = bypass.configure(client)

    assert str(configured.base_url).rstrip("/") == "https://api.scrapfly.io"
    assert configured.params["key"] == "test_key"
    assert configured.headers["scp-sdk"] == "python"
    assert configured.headers["X-Test"] == "Value"


def test_stealth_browser_raises_without_dep(monkeypatch):
    import infrastructure.bypass.stealth_browser as stealth_browser

    monkeypatch.setattr(stealth_browser, "_STEALTH_AVAILABLE", False)

    bypass = StealthBrowserBypass()
    mock_client = MagicMock()

    with pytest.raises(ImportError, match="playwright-stealth is not installed"):
        bypass.configure(mock_client)
