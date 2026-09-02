import asyncio
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


@pytest.mark.asyncio
async def test_nodriver_serializes_same_persistent_profile(monkeypatch, tmp_path):
    import job_ftch.infrastructure.bypass.nodriver_bypass as nodriver_bypass

    active_starts = 0
    max_active_starts = 0

    class _FakeBrowser:
        async def get(self, url):
            del url
            return MagicMock(set_window_size=MagicMock())

        def stop(self):
            nonlocal active_starts
            active_starts -= 1

    class _FakeNodriver:
        async def start(self, **kwargs):
            nonlocal active_starts, max_active_starts
            assert kwargs["user_data_dir"] == str((tmp_path / "profile").resolve())
            active_starts += 1
            max_active_starts = max(max_active_starts, active_starts)
            await asyncio.sleep(0.02)
            return _FakeBrowser()

    async def _open_once() -> None:
        async with nodriver_bypass.NodriverBypass().open_page(
            {"persistent_context": True, "_profile_dir": str(tmp_path / "profile")}
        ):
            await asyncio.sleep(0.02)

    monkeypatch.setattr(nodriver_bypass, "nodriver", _FakeNodriver())
    await asyncio.gather(_open_once(), _open_once())

    assert max_active_starts == 1


@pytest.mark.asyncio
async def test_nodriver_recovers_corrupt_shared_profile(monkeypatch, tmp_path):
    import job_ftch.infrastructure.bypass.nodriver_bypass as nodriver_bypass

    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "stale-lock").write_text("locked", encoding="utf-8")
    starts: list[str] = []

    class _FakeBrowser:
        async def get(self, url):
            del url
            return MagicMock(set_window_size=MagicMock())

        def stop(self):
            return None

    class _FakeNodriver:
        async def start(self, **kwargs):
            starts.append(kwargs["user_data_dir"])
            if len(starts) <= 2:
                raise Exception("Failed to connect to browser")
            return _FakeBrowser()

    monkeypatch.setattr(nodriver_bypass, "nodriver", _FakeNodriver())

    async with nodriver_bypass.NodriverBypass().open_page(
        {"persistent_context": True, "_profile_dir": str(profile), "sandbox": False}
    ):
        pass

    assert starts[0] == str(profile.resolve())
    assert starts[1] == starts[0]
    assert len(starts) == 3
    assert starts[2] != starts[0]
    assert starts[2].startswith("nodriver_recovery_profile_") or starts[2].split("\\")[
        -1
    ].startswith("nodriver_recovery_profile_")
    quarantined = list((tmp_path / "_quarantine").glob("profile.*"))
    assert len(quarantined) == 1
    assert (quarantined[0] / "stale-lock").exists()


@pytest.mark.asyncio
async def test_nodriver_keeps_shared_profile_when_clean_recovery_also_fails(monkeypatch, tmp_path):
    import job_ftch.infrastructure.bypass.nodriver_bypass as nodriver_bypass

    profile = tmp_path / "profile"
    profile.mkdir()

    class _FakeNodriver:
        async def start(self, **kwargs):
            del kwargs
            raise Exception("Failed to connect to browser")

    monkeypatch.setattr(nodriver_bypass, "nodriver", _FakeNodriver())

    with pytest.raises(Exception, match="Failed to connect"):
        async with nodriver_bypass.NodriverBypass().open_page(
            {"persistent_context": True, "_profile_dir": str(profile), "sandbox": False}
        ):
            pass

    assert profile.exists()
    assert not (tmp_path / "_quarantine").exists()


@pytest.mark.asyncio
async def test_nodriver_applies_persona_user_agent_to_network_launch_args(monkeypatch, tmp_path):
    import job_ftch.infrastructure.bypass.nodriver_bypass as nodriver_bypass

    captured_args: list[str] = []

    class _FakeBrowser:
        async def get(self, url):
            del url
            return MagicMock(set_window_size=MagicMock())

        def stop(self):
            return None

    class _FakeNodriver:
        async def start(self, **kwargs):
            captured_args.extend(kwargs["browser_args"])
            return _FakeBrowser()

    monkeypatch.setattr(nodriver_bypass, "nodriver", _FakeNodriver())

    async with nodriver_bypass.NodriverBypass().open_page(
        {
            "persistent_context": True,
            "_profile_dir": str(tmp_path / "profile"),
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/146.0.0.0",
            "_persona_user_agent": True,
        }
    ):
        pass

    assert (
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/146.0.0.0" in captured_args
    )


def test_cloak_without_dependency_or_executable_degrades_without_metadata(monkeypatch):
    import job_ftch.infrastructure.bypass.cloak_bypass as cloak_bypass

    monkeypatch.setattr(cloak_bypass, "_CLOAK_AVAILABLE", False)
    kwargs = {"headless": True, "args": []}
    assert cloak_bypass.CloakBrowserBypass().apply_browser_args(kwargs) == kwargs
    assert "_cloakbrowser_backend" not in kwargs


@pytest.mark.asyncio
async def test_nodriver_response_exposes_playwright_like_headers_and_body():
    from job_ftch.infrastructure.bypass.nodriver_bypass import _NodriverResponse

    response = _NodriverResponse(
        url="https://example.test",
        status=403,
        headers={"content-type": "text/html"},
        body="<html>blocked</html>",
    )

    assert response.headers == {"content-type": "text/html"}
    assert await response.all_headers() == {"content-type": "text/html"}
    assert await response.body() == b"<html>blocked</html>"


def test_nodriver_evaluate_invokes_playwright_style_function_strings():
    from job_ftch.infrastructure.bypass.nodriver_bypass import _playwright_eval_expression

    assert _playwright_eval_expression("() => 1") == "(() => 1)()"
    assert _playwright_eval_expression("async () => 1") == "(async () => 1)()"
    assert _playwright_eval_expression("document.readyState") == "document.readyState"
