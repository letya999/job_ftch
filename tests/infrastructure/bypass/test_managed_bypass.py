from unittest.mock import MagicMock

import httpx
import pytest

from job_ftch.infrastructure.bypass.managed import ManagedScraperBypass
from job_ftch.infrastructure.bypass.noop import NoopBypass
from job_ftch.infrastructure.bypass.stealth_browser import StealthBrowserBypass


@pytest.mark.asyncio
async def test_noop_bypass_returns_client_unchanged():
    client = httpx.AsyncClient()
    bypass = NoopBypass()
    assert await bypass.apply_http(client) is client


@pytest.mark.asyncio
async def test_managed_scraper_bypass_scrapfly_sets_headers():
    bypass = ManagedScraperBypass(
        api_url="https://api.scrapfly.io",
        api_key="fixture-api-key",  # pragma: allowlist secret
        provider="scrapfly",  # pragma: allowlist secret
    )
    client = httpx.AsyncClient(headers={"X-Test": "Value"})
    configured = await bypass.apply_http(client)

    assert str(configured.base_url).rstrip("/") == "https://api.scrapfly.io"
    assert configured.params["key"] == "fixture-api-key"  # pragma: allowlist secret
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


@pytest.mark.asyncio
async def test_curl_bypass_raises_without_optional_dependency(monkeypatch):
    import job_ftch.infrastructure.bypass.curl_bypass as curl_bypass

    monkeypatch.setattr(curl_bypass, "_CurlSession", None)
    with pytest.raises(ImportError, match="stealth.*extra"):
        await curl_bypass.CurlBypass().apply_http(MagicMock())


@pytest.mark.asyncio
async def test_camoufox_raises_without_optional_dependency(monkeypatch):
    import job_ftch.infrastructure.bypass.camoufox_bypass as camoufox_bypass

    monkeypatch.setattr(camoufox_bypass, "AsyncCamoufox", None)
    manager = camoufox_bypass.CamoufoxBypass().open_page({})
    with pytest.raises(ImportError, match="Camoufox bypass requires"):
        await manager.__aenter__()


@pytest.mark.asyncio
async def test_nodriver_raises_without_optional_dependency(monkeypatch):
    import job_ftch.infrastructure.bypass.nodriver_bypass as nodriver_bypass

    monkeypatch.setattr(nodriver_bypass, "nodriver", None)
    manager = nodriver_bypass.NodriverBypass().open_page({})
    with pytest.raises(ImportError, match="nodriver bypass requires"):
        await manager.__aenter__()


def test_cloak_without_dependency_or_executable_degrades_without_metadata(monkeypatch):
    import job_ftch.infrastructure.bypass.cloak_bypass as cloak_bypass

    monkeypatch.setattr(cloak_bypass, "_CLOAK_AVAILABLE", False)
    kwargs = {"headless": True, "args": []}
    assert cloak_bypass.CloakBrowserBypass().apply_browser_args(kwargs) == kwargs
    assert "_cloakbrowser_backend" not in kwargs
