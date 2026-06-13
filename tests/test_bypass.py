import pytest

import job_ftch.infrastructure.bypass.curl_bypass as curl_bypass_module
from job_ftch.application.registry import resolve_bypass
from job_ftch.infrastructure.bypass.adaptive import AdaptiveBypassManager
from job_ftch.infrastructure.bypass.behavior_sim import BehaviorSimBypass
from job_ftch.infrastructure.bypass.cloak_bypass import CloakBrowserBypass
from job_ftch.infrastructure.bypass.curl_bypass import CurlBypass
from job_ftch.infrastructure.bypass.noop import NoopBypass
from job_ftch.infrastructure.bypass.proxy_rotator import ProxyRotatorBypass
from job_ftch.infrastructure.bypass.stealth_browser import StealthBrowserBypass


@pytest.mark.asyncio
async def test_resolve_noop_bypass():
    bypass = resolve_bypass("noop")
    assert isinstance(bypass, NoopBypass)

    # Should not alter client or args
    assert await bypass.apply_http("mock_client") == "mock_client"
    assert bypass.apply_browser_args({"args": []}) == {"args": []}


@pytest.mark.asyncio
async def test_resolve_proxy_rotator():
    config = {"proxy_list": "http://proxy1:8080, http://proxy2:8080"}
    bypass = resolve_bypass("proxy_rotator", bypass_config=config)
    assert isinstance(bypass, ProxyRotatorBypass)

    # Check browser args application
    kwargs = bypass.apply_browser_args({})
    assert "proxy" in kwargs
    assert kwargs["proxy"]["server"] == "http://proxy1:8080"

    kwargs2 = bypass.apply_browser_args({})
    assert kwargs2["proxy"]["server"] == "http://proxy2:8080"


@pytest.mark.asyncio
async def test_resolve_stealth_browser():
    bypass = resolve_bypass("stealth_browser")
    assert isinstance(bypass, StealthBrowserBypass)

    kwargs = bypass.apply_browser_args({"args": ["--headless"]})
    assert "--disable-blink-features=AutomationControlled" in kwargs["args"]


@pytest.mark.asyncio
async def test_resolve_curl_bypass():
    bypass = resolve_bypass("curl_stealth", bypass_config={"impersonate": "safari15_3"})
    assert isinstance(bypass, CurlBypass)
    assert bypass.impersonate == "safari15_3"

    class _FakeSession:
        def __init__(self, *, impersonate: str) -> None:
            self.impersonate = impersonate

        async def get(self, url: str, allow_redirects: bool = False, **kwargs: object) -> object:
            return {"url": url, "allow_redirects": allow_redirects, "kwargs": kwargs}

        async def close(self) -> None:
            return None

    class _FakeRequests:
        @staticmethod
        def AsyncSession(*, impersonate: str) -> _FakeSession:
            return _FakeSession(impersonate=impersonate)

    original_requests = curl_bypass_module.requests
    original_import_error = curl_bypass_module._IMPORT_ERROR
    curl_bypass_module.requests = _FakeRequests()
    curl_bypass_module._IMPORT_ERROR = None
    try:
        res = await bypass.apply_http(None)
    finally:
        curl_bypass_module.requests = original_requests
        curl_bypass_module._IMPORT_ERROR = original_import_error

    assert hasattr(res, "get")


@pytest.mark.asyncio
async def test_resolve_cloak_bypass():
    bypass = resolve_bypass("cloak", bypass_config={"executable_path": "/mock/path"})
    assert isinstance(bypass, CloakBrowserBypass)

    kwargs = bypass.apply_browser_args({"args": []})
    assert kwargs["executable_path"] == "/mock/path"
    assert "--humanize=true" in kwargs["args"]


@pytest.mark.asyncio
async def test_resolve_behavior_sim():
    bypass = resolve_bypass("behavior_sim", bypass_config={"min_delay": "0.1", "max_delay": "0.2"})
    assert isinstance(bypass, BehaviorSimBypass)
    assert bypass._min_delay == 0.1


@pytest.mark.asyncio
async def test_resolve_adaptive_bypass():
    bypass = resolve_bypass("auto", bypass_config={"impersonate": "safari15_3"})
    assert isinstance(bypass, AdaptiveBypassManager)

    assert bypass.current_name == "noop"

    assert bypass.escalate() is True
    assert bypass.current_name == "curl_stealth"

    assert bypass.escalate() is True
    assert bypass.current_name == "stealth_browser"

    assert bypass.escalate() is True
    assert bypass.current_name == "cloak"

    assert bypass.escalate() is False
    assert bypass.current_name == "cloak"
