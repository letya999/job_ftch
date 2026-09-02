import httpx
import pytest

import job_ftch.infrastructure.bypass.curl_bypass as curl_bypass_module
from job_ftch.application.registry import resolve_bypass
from job_ftch.infrastructure.bypass.adaptive import AdaptiveBypassManager
from job_ftch.infrastructure.bypass.behavior_sim import BehaviorSimBypass
from job_ftch.infrastructure.bypass.camoufox_bypass import CamoufoxBypass
from job_ftch.infrastructure.bypass.cloak_bypass import CloakBrowserBypass
from job_ftch.infrastructure.bypass.curl_bypass import CurlBypass
from job_ftch.infrastructure.bypass.nodriver_bypass import NodriverBypass
from job_ftch.infrastructure.bypass.noop import NoopBypass
from job_ftch.infrastructure.bypass.patchright_bypass import PatchrightBrowserBypass
from job_ftch.infrastructure.bypass.stealth_browser import StealthBrowserBypass
from job_ftch.infrastructure.sources.site_fingerprinter import fingerprint


def test_curl_cffi_security_fixed_release_loads_on_python_312() -> None:
    """The pinned curl-cffi release must load on Python 3.12."""
    assert curl_bypass_module._CurlSession is not None


@pytest.mark.asyncio
async def test_resolve_noop_bypass() -> None:
    bypass = resolve_bypass("noop")
    assert isinstance(bypass, NoopBypass)

    # Should not alter client or args
    assert await bypass.apply_http("mock_client") == "mock_client"
    assert bypass.apply_browser_args({"args": []}) == {"args": []}


@pytest.mark.asyncio
async def test_resolve_stealth_browser() -> None:
    bypass = resolve_bypass("stealth_browser")
    assert isinstance(bypass, StealthBrowserBypass)

    kwargs = bypass.apply_browser_args({"args": ["--headless"]})
    assert "--disable-blink-features=AutomationControlled" in kwargs["args"]
    assert "--disable-web-security" not in kwargs["args"]


def test_patchright_projects_identity_to_worker_bootstrap_requests() -> None:
    result = PatchrightBrowserBypass().apply_browser_args(
        {
            "args": [],
            "_process_identity_user_agent": "Mozilla/5.0 Chrome/150.0.0.0",
            "_process_identity_locale": "de-DE",
        }
    )

    assert "_process_identity_user_agent" not in result
    assert "_process_identity_locale" not in result
    assert "--user-agent=Mozilla/5.0 Chrome/150.0.0.0" in result["args"]
    assert "--lang=de-DE" in result["args"]


@pytest.mark.asyncio
async def test_resolve_curl_bypass() -> None:
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

    original_session_cls = curl_bypass_module._CurlSession
    original_import_error = curl_bypass_module._IMPORT_ERROR
    curl_bypass_module._CurlSession = _FakeSession
    curl_bypass_module._IMPORT_ERROR = None
    try:
        res = await bypass.apply_http(None)
    finally:
        curl_bypass_module._CurlSession = original_session_cls
        curl_bypass_module._IMPORT_ERROR = original_import_error

    assert hasattr(res, "get")


@pytest.mark.asyncio
async def test_curl_bypass_preserves_timeout_from_source_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bypass = resolve_bypass("curl_stealth")

    captured: dict[str, object] = {}

    class _FakeSession:
        async def get(self, url: str, allow_redirects: bool = False, **kwargs: object) -> object:
            captured["url"] = url
            captured["allow_redirects"] = allow_redirects
            captured["kwargs"] = kwargs
            return {"ok": True}

        async def close(self) -> None:
            return None

    class _FakeTimeout:
        read = 12.5
        connect = 9.0
        write = 9.0
        pool = 9.0

    class _FakeClient:
        timeout = _FakeTimeout()

    def _fake_async_session(*, impersonate: str, **kwargs: object) -> _FakeSession:
        del kwargs
        captured["impersonate"] = impersonate
        return _FakeSession()

    original_session_cls = curl_bypass_module._CurlSession
    original_import_error = curl_bypass_module._IMPORT_ERROR
    curl_bypass_module._CurlSession = _fake_async_session
    curl_bypass_module._IMPORT_ERROR = None
    monkeypatch.setattr(
        "job_ftch.infrastructure.network.ssrf_guard.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )
    try:
        client = await bypass.apply_http(_FakeClient())
        await client.get("https://example.com/jobs", follow_redirects=True)
    finally:
        curl_bypass_module._CurlSession = original_session_cls
        curl_bypass_module._IMPORT_ERROR = original_import_error

    assert captured["impersonate"] in ("chrome", "chrome146", "chrome145")
    assert captured["url"] == "https://example.com/jobs"
    assert captured["allow_redirects"] is True
    assert captured["kwargs"] == {"timeout": 12.5}


@pytest.mark.asyncio
async def test_fingerprint_preserves_prepared_runtime_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, int] = {"get": 0}

    class _WrappedClient:
        async def get(
            self, url: str, *, follow_redirects: bool = False, **kwargs: object
        ) -> httpx.Response:
            del kwargs
            calls["get"] += 1
            request = httpx.Request("GET", url)
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<html><body><div id='root'></div></body></html>",
                request=request,
            )

    monkeypatch.setattr(
        "job_ftch.application.registry.resolve_bypass",
        lambda name: pytest.fail(f"prepared client must not be rewrapped with {name}"),
    )

    profile = await fingerprint("https://example.com/jobs", client=_WrappedClient())

    assert calls == {"get": 1}
    assert profile.detected_config == {"render": True}


@pytest.mark.asyncio
async def test_resolve_cloak_bypass() -> None:
    bypass = resolve_bypass("cloak", bypass_config={"executable_path": "/mock/path"})
    assert isinstance(bypass, CloakBrowserBypass)

    kwargs = bypass.apply_browser_args({"args": []})
    assert kwargs["executable_path"] == "/mock/path"
    assert "--humanize=true" in kwargs["args"]


def test_cloak_persistent_args_keep_backend_settings() -> None:
    bypass = CloakBrowserBypass(executable_path="C:/cloak/chrome.exe", headless=False)
    result = bypass.apply_browser_args(
        {
            "user_agent": "test-agent",
            "viewport": {"width": 1200, "height": 800},
            "headless": True,
            "args": [],
        }
    )
    assert result["executable_path"] == "C:/cloak/chrome.exe"
    assert result["headless"] is False
    assert "--humanize=true" in result["args"]


def test_cloak_non_persistent_args_apply_explicit_headless_policy() -> None:
    bypass = CloakBrowserBypass(
        executable_path="C:/cloak/chrome.exe",
        backend="playwright",
        headless=True,
    )
    result = bypass.apply_browser_args({"args": []})
    assert result["headless"] is True
    assert result["executable_path"] == "C:/cloak/chrome.exe"
    assert "_cloakbrowser_backend" not in result


def test_ordinary_launcher_consumes_internal_cloak_metadata() -> None:
    from types import SimpleNamespace

    from job_ftch.infrastructure.sources.browser_utils import _playwright_launcher

    chromium = object()
    playwright = SimpleNamespace(chromium=chromium, firefox=object(), webkit=object())
    kwargs = {
        "_cloakbrowser_backend": "patchright",
        "_patchright_required": True,
        "geoip": True,
    }
    assert _playwright_launcher(playwright, kwargs) is chromium
    assert "_cloakbrowser_backend" not in kwargs
    assert "_patchright_required" not in kwargs
    assert "geoip" not in kwargs


@pytest.mark.asyncio
async def test_resolve_behavior_sim() -> None:
    bypass = resolve_bypass("behavior_sim", bypass_config={"min_delay": "0.1", "max_delay": "0.2"})
    assert isinstance(bypass, BehaviorSimBypass)
    assert bypass._min_delay == 0.1


@pytest.mark.asyncio
async def test_resolve_adaptive_bypass() -> None:
    bypass = resolve_bypass("auto", bypass_config={"impersonate": "safari15_3"})
    assert isinstance(bypass, AdaptiveBypassManager)

    assert bypass.current_name == "noop"

    for expected_name in bypass.available_tiers[1:]:
        assert bypass.escalate() is True
        assert bypass.current_name == expected_name

    assert bypass.escalate() is False
    assert bypass.current_name == bypass.available_tiers[-1]


def test_create_camoufox_bypass_class() -> None:
    bypass = CamoufoxBypass()
    assert bypass.__class__.__name__ == "CamoufoxBypass"


def test_create_nodriver_bypass_class() -> None:
    bypass = NodriverBypass()
    assert bypass.__class__.__name__ == "NodriverBypass"


@pytest.mark.asyncio
async def test_camoufox_bypass_preserves_browser_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _FakePage:
        async def goto(self, url: str) -> None:
            captured["warmup_url"] = url

        async def close(self) -> None:
            captured["page_closed"] = True

    class _FakeContext:
        def __init__(self) -> None:
            self.page = _FakePage()

        async def add_cookies(self, cookies: list[dict[str, object]]) -> None:
            captured["cookies"] = cookies

        def set_default_timeout(self, timeout: int) -> None:
            captured["timeout"] = timeout

        async def new_page(self) -> _FakePage:
            return self.page

        async def close(self) -> None:
            captured["context_closed"] = True

    class _FakeBrowser:
        def __init__(self) -> None:
            self.context = _FakeContext()

        async def new_context(self, **kwargs: object) -> _FakeContext:
            captured["context_kwargs"] = kwargs
            return self.context

    class _FakeCamoufox:
        def __init__(self, **kwargs: object) -> None:
            captured["browser_kwargs"] = kwargs

        async def __aenter__(self) -> _FakeBrowser:
            return _FakeBrowser()

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            del exc_type, exc, tb

    monkeypatch.setattr(
        "job_ftch.infrastructure.bypass.camoufox_bypass.AsyncCamoufox", _FakeCamoufox
    )
    monkeypatch.setattr(
        "job_ftch.infrastructure.bypass.camoufox_bypass.get_settings",
        lambda: type("S", (), {"browser_default_timeout_ms": 4321})(),
    )
    monkeypatch.setenv("JOB_FTCH_HTTP_PROXY", "http://proxy.local:8080")

    bypass = CamoufoxBypass()
    config = {
        "headless": False,
        "locale": "ru-RU",
        "viewport": {"width": 1600, "height": 900},
        "user_agent": "Agent/1.0",
        "skip_ssl": True,
        "timeout": 9876,
        "cookies": [{"name": "cf_clearance", "value": "ok"}],
        "warmup_url": "https://example.com/warmup",
    }

    async with bypass.open_page(config, use_proxy=True):
        pass

    # With a proxy active, Camoufox owns identity natively (defect A9): geoip
    # derives locale/timezone from the exit IP, WebRTC is blocked to prevent an
    # IP leak, the cursor is humanized, and our configured ``locale`` is NOT
    # forced (the exit IP is the single source of truth).
    assert captured["browser_kwargs"] == {
        "headless": False,
        "humanize": True,
        "geoip": True,
        "block_webrtc": True,
        "window": (1600, 900),
        "proxy": {"server": "http://proxy.local:8080"},
    }
    # No user_agent override: forcing a Chromium persona UA onto a Firefox
    # engine is an instant cross-check failure. No locale either while geoip owns it.
    assert captured["context_kwargs"] == {
        "viewport": {"width": 1600, "height": 900},
        "ignore_https_errors": True,
    }
    assert captured["timeout"] == 9876
    assert captured["cookies"] == [{"name": "cf_clearance", "value": "ok"}]
    assert captured["warmup_url"] == "https://example.com/warmup"


@pytest.mark.asyncio
async def test_nodriver_bypass_preserves_browser_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _FakeTab:
        url = "about:blank"

        async def set_window_size(self, x: int, y: int, width: int, height: int) -> None:
            captured["window_size"] = (x, y, width, height)

        async def close(self) -> None:
            captured["tab_closed"] = True

    class _FakeBrowser:
        async def get(self, url: str) -> _FakeTab:
            captured["get_url"] = url
            return _FakeTab()

        async def create_context(
            self,
            *,
            proxy_server: str,
            proxy_bypass_list: str,
        ) -> _FakeTab:
            captured["proxy_context"] = {
                "proxy_server": proxy_server,
                "proxy_bypass_list": proxy_bypass_list,
            }
            return _FakeTab()

        async def stop(self) -> None:
            captured["stop"] = True

    async def _fake_start(**kwargs: object) -> _FakeBrowser:
        captured["start_kwargs"] = kwargs
        return _FakeBrowser()

    monkeypatch.setattr(
        "job_ftch.infrastructure.bypass.nodriver_bypass.nodriver",
        type("ND", (), {"start": staticmethod(_fake_start)})(),
    )
    monkeypatch.setattr(
        "job_ftch.infrastructure.bypass.nodriver_bypass.get_settings",
        lambda: type("S", (), {"browser_default_timeout_ms": 4321})(),
    )
    monkeypatch.setenv("JOB_FTCH_HTTP_PROXY", "http://proxy.local:8080")

    bypass = NodriverBypass(
        browser_args=["--existing-flag", "--user-agent=Old/1.0", "--lang=de-DE"],
        lang="en-US",
    )
    config = {
        "headless": False,
        "disable_http2": True,
        "skip_ssl": True,
        "user_agent": "Agent/2.0",
        "locale": "ru-RU",
        "viewport": {"width": 1440, "height": 900},
    }

    async with bypass.open_page(config, use_proxy=True):
        pass

    assert captured["start_kwargs"] == {
        "headless": False,
        "user_data_dir": None,
        "browser_executable_path": None,
        "browser_args": [
            "--existing-flag",
            "--disable-http2",
            "--ignore-certificate-errors",
            "--user-agent=Agent/2.0",
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-site-isolation-trials",
            "--disable-features=TranslateUI",
            "--disable-background-networking",
            "--disable-default-apps",
            "--disable-extensions",
            "--disable-sync",
            "--metrics-recording-only",
            "--no-first-run",
            "--safebrowsing-disable-auto-update",
            "--window-size=1440,900",
        ],
        "sandbox": True,
        "lang": "en-US",
    }
    assert captured["proxy_context"] == {
        "proxy_server": "http://proxy.local:8080",
        "proxy_bypass_list": "localhost",
    }
    assert captured["window_size"] == (0, 0, 1440, 900)


def test_failure_signal_ddos_guard_is_captcha() -> None:
    from job_ftch.infrastructure.bypass.failure_signal import (
        FailureKind,
        HeuristicFailureSignal,
    )

    assert (
        HeuristicFailureSignal().classify(
            status_code=200,
            body=b"<div>ddos-guard</div>",
            error=None,
        )
        == FailureKind.CHALLENGE
    )
    assert (
        HeuristicFailureSignal().classify(
            status_code=200,
            body="Подтвердите, что вы не робот".encode(),
            error=None,
        )
        == FailureKind.CAPTCHA
    )
