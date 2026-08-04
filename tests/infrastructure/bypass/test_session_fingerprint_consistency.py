from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from job_ftch.infrastructure.bypass.adaptive import AdaptiveBypassManager
from job_ftch.infrastructure.bypass.persona import (
    PERSONA_POOL,
    align_persona_version,
    select_persona,
)
from job_ftch.infrastructure.bypass.session_handoff import HandoffState, SessionHandoff
from job_ftch.infrastructure.bypass.stealth_hardening import (
    apply_persona_hardening,
    apply_stealth_hardening,
)


def test_every_persona_is_an_os_browser_renderer_tuple() -> None:
    for persona in PERSONA_POOL:
        if persona.browser_family == "safari":
            assert "Macintosh" in persona.ua
            assert persona.navigator_platform == "MacIntel"
            assert persona.webgl_renderer == "Apple GPU"
            assert persona.sec_ch_ua == ""
        if "Windows" in persona.sec_ch_ua_platform:
            assert "Windows NT" in persona.ua
            assert persona.webgl_renderer.startswith("ANGLE")
        if "Linux" in persona.sec_ch_ua_platform:
            assert "Linux" in persona.ua
            assert persona.webgl_renderer.startswith("Mesa")
        if persona.browser_family == "chromium":
            ua_major = re.search(r"Chrome/(\d+)", persona.ua)
            assert ua_major is not None
            assert f'v="{ua_major.group(1)}"' in persona.sec_ch_ua


def test_persona_is_sticky_per_domain_and_requested_family() -> None:
    first = select_persona("example.test", "firefox")
    second = select_persona("example.test", "firefox")
    chromium = select_persona("example.test", "chromium")
    assert first is second
    assert first.browser_family == "firefox"
    assert chromium.browser_family == "chromium"


def test_runtime_browser_version_realigns_ua_and_client_hints() -> None:
    persona = select_persona("runtime-version.test", "chromium")
    aligned = align_persona_version(persona, "chromium", "142.0.7312.10")
    assert "Chrome/142" in aligned.ua
    assert '"Chromium";v="142"' in aligned.sec_ch_ua
    assert '"Google Chrome";v="142"' in aligned.sec_ch_ua
    assert aligned.browser_version == "142"


@pytest.mark.asyncio
async def test_non_chromium_hardening_does_not_inject_chrome_surface() -> None:
    page = SimpleNamespace(add_init_script=AsyncMock())
    persona = select_persona("firefox.test", "firefox")
    await apply_persona_hardening(page, persona)
    script = page.add_init_script.await_args.args[0]
    assert "window.chrome" not in script
    assert "userAgentData" not in script
    assert "const touched = new WeakSet()" in script


@pytest.mark.asyncio
async def test_fingerprint_harness_covers_stable_canvas_audio_and_web_api_shapes() -> None:
    page = SimpleNamespace(add_init_script=AsyncMock())
    await apply_stealth_hardening(page, canvas_seed=42, browser_family="chromium")
    script = page.add_init_script.await_args.args[0]
    assert "CanvasRenderingContext2D.prototype.getImageData" in script
    assert "AudioBuffer.prototype.getChannelData" in script
    assert "navigator.permissions.query" in script
    assert "navigator.getBattery" in script
    assert "navigator.mediaDevices.enumerateDevices" in script
    assert "window.speechSynthesis.getVoices" in script
    assert "userAgentData" in script


@pytest.mark.asyncio
async def test_hardening_does_not_window_only_override_worker_readable_scalars() -> None:
    # TRACK A5: navigator.hardwareConcurrency / deviceMemory must NOT be patched
    # in an init script - it would not reach dedicated Workers, so the window and
    # worker realms would disagree (a fingerprint leak). They are left real,
    # which is coherent across all realms.
    page = SimpleNamespace(add_init_script=AsyncMock())
    await apply_stealth_hardening(
        page,
        canvas_seed=42,
        browser_family="chromium",
        hardware_concurrency=8,
        device_memory=8,
    )
    script = page.add_init_script.await_args.args[0]
    assert "'hardwareConcurrency'" not in script
    assert "'deviceMemory'" not in script


@pytest.mark.asyncio
async def test_proxy_hardening_blocks_direct_webrtc_candidates() -> None:
    page = SimpleNamespace(add_init_script=AsyncMock())
    persona = select_persona("proxy.test", "chromium")
    await apply_persona_hardening(page, persona, proxy_active=True)
    script = page.add_init_script.await_args.args[0]
    assert "window.chrome" in script
    assert "iceTransportPolicy: 'relay'" in script


@pytest.mark.asyncio
async def test_clearance_cookie_continues_from_listing_to_details() -> None:
    manager = AdaptiveBypassManager()
    page = SimpleNamespace(
        context=SimpleNamespace(
            cookies=AsyncMock(
                return_value=[
                    {
                        "name": "cf_clearance",
                        "value": "secret-cookie-value",
                        "domain": ".example.test",
                        "path": "/",
                    },
                    {
                        "name": "analytics_id",
                        "value": "must-not-carry",
                        "domain": ".example.test",
                        "path": "/",
                    },
                ]
            )
        )
    )

    await manager.capture_session_state(page)
    detail_config = manager.prepare_browser_config({"url": "https://example.test/job/1"})

    assert [cookie["name"] for cookie in detail_config["cookies"]] == ["cf_clearance"]
    assert manager.route_state.session.value == "sticky"


@pytest.mark.asyncio
async def test_listing_pagination_and_details_share_identity_and_profile() -> None:
    manager = AdaptiveBypassManager()
    context = SimpleNamespace(
        persona=select_persona("continuity.test", "chromium"),
        preflight=SimpleNamespace(tier="adaptive", network="direct"),
        proxy_available=False,
        set_effective_route=MagicMock(),
        set_browser_family=MagicMock(),
    )
    manager.bind_context(context)
    page = SimpleNamespace(
        context=SimpleNamespace(
            cookies=AsyncMock(
                return_value=[
                    {
                        "name": "cf_clearance",
                        "value": "continuity-cookie",
                        "domain": ".continuity.test",
                        "path": "/",
                    }
                ]
            )
        )
    )
    await manager.capture_session_state(page)
    generation = manager.route_state.generation

    configs = [
        manager.prepare_browser_config({"url": url, "persistent_context": True})
        for url in (
            "https://continuity.test/jobs",
            "https://continuity.test/jobs?page=2",
            "https://continuity.test/jobs/1",
            "https://continuity.test/jobs/2",
        )
    ]

    assert len({config["_profile_dir"] for config in configs}) == 1
    assert all(config["cookies"][0]["name"] == "cf_clearance" for config in configs)
    assert manager.route_state.generation == generation
    assert context.persona is select_persona("continuity.test", "chromium")
    await manager.close()


@pytest.mark.asyncio
async def test_proxy_change_invalidates_clearance_cookie_session() -> None:
    manager = AdaptiveBypassManager()
    manager._session_cookies = [{"name": "cf_clearance", "value": "test"}]
    context = SimpleNamespace(
        proxy_available=True,
        current_proxy_url="http://proxy.test:8080",
        set_effective_route=MagicMock(),
        set_browser_family=MagicMock(),
    )
    manager.bind_context(context)

    generation = manager.route_state.generation
    assert manager.activate_proxy()
    assert manager.prepare_browser_config({}).get("cookies") is None
    assert manager.route_state.generation > generation
    assert manager.route_state.session.value == "fresh"


@pytest.mark.asyncio
async def test_handoff_uses_independent_origin_locks() -> None:
    handoff = SessionHandoff.__new__(SessionHandoff)
    handoff._state = {}
    handoff._locks = {}
    concurrent = 0
    peak = 0

    async def harvest(url: str) -> HandoffState:
        nonlocal concurrent, peak
        del url
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(0)
        concurrent -= 1
        return HandoffState(harvested_at=asyncio.get_running_loop().time())

    handoff._harvest_state = harvest  # type: ignore[method-assign]
    handoff._reinject = AsyncMock()  # type: ignore[method-assign]
    session = SimpleNamespace(get=AsyncMock(return_value="ok"))
    await asyncio.gather(
        handoff._do_handoff("https://one.test/x", session, "GET", {}),
        handoff._do_handoff("https://two.test/x", session, "GET", {}),
    )
    assert peak == 2


@pytest.mark.asyncio
async def test_handoff_does_not_hold_origin_lock_while_harvesting() -> None:
    handoff = SessionHandoff.__new__(SessionHandoff)
    handoff._state = {}
    handoff._locks = {}
    handoff._inflight = {}
    lock_was_held = True

    async def harvest(url: str) -> HandoffState:
        nonlocal lock_was_held
        del url
        lock_was_held = handoff._locks["one.test"].locked()
        await asyncio.sleep(0)
        return HandoffState(harvested_at=asyncio.get_running_loop().time())

    handoff._harvest_state = harvest  # type: ignore[method-assign]
    handoff._reinject = AsyncMock()  # type: ignore[method-assign]
    session = SimpleNamespace(get=AsyncMock(return_value="ok"))
    await handoff._do_handoff("https://one.test/x", session, "GET", {})
    assert not lock_was_held
    assert not handoff._inflight


@pytest.mark.asyncio
async def test_handoff_reinjects_into_real_curl_session_map() -> None:
    jar_one = MagicMock()
    jar_two = MagicMock()
    adapter = SimpleNamespace(
        _sessions={
            "chrome": SimpleNamespace(cookies=jar_one),
            "firefox": SimpleNamespace(cookies=jar_two),
        }
    )
    handoff = SessionHandoff.__new__(SessionHandoff)
    state = HandoffState(
        cookies=[
            {
                "name": "cf_clearance",
                "value": "test-value",
                "domain": ".example.test",
                "path": "/",
            }
        ]
    )
    await handoff._reinject(adapter, state)
    jar_one.set.assert_called_once()
    jar_two.set.assert_called_once()


@pytest.mark.asyncio
async def test_unsafe_post_is_warmed_but_not_replayed() -> None:
    handoff = SessionHandoff.__new__(SessionHandoff)
    handoff._state = {"example.test": HandoffState(harvested_at=asyncio.get_running_loop().time())}
    handoff._locks = {}
    handoff._reinject = AsyncMock()  # type: ignore[method-assign]
    session = SimpleNamespace(post=AsyncMock(return_value="replayed"))
    original = object()
    result = await handoff._do_handoff(
        "https://example.test/api",
        session,
        "POST",
        {},
        replay_safe=False,
        original_response=original,
    )
    assert result is original
    session.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_storage_handoff_stays_in_browser_instead_of_lossy_replay() -> None:
    handoff = SessionHandoff.__new__(SessionHandoff)
    handoff._state = {
        "example.test": HandoffState(
            local_storage={"session": "browser-only"},
            harvested_at=asyncio.get_running_loop().time(),
        )
    }
    handoff._locks = {}
    handoff._reinject = AsyncMock()  # type: ignore[method-assign]
    session = SimpleNamespace(get=AsyncMock(return_value="replayed"))
    original = object()
    result = await handoff._do_handoff(
        "https://example.test/api",
        session,
        "GET",
        {},
        original_response=original,
    )
    assert result is original
    session.get.assert_not_awaited()
