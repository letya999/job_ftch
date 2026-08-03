"""Adaptive bypass escalation contracts."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from job_ftch.application.registry import BypassCapability
from job_ftch.infrastructure.bypass import adaptive
from job_ftch.infrastructure.bypass.adaptive import AdaptiveBypassManager
from job_ftch.infrastructure.bypass.captcha_models import CaptchaSolveResult
from job_ftch.infrastructure.bypass.failure_signal import FailureKind
from job_ftch.infrastructure.sources.source_deadline import source_deadline_scope


class _Strategy:
    async def apply_http(self, client: object) -> object:
        return client

    def apply_browser_args(self, kwargs: dict[str, object]) -> dict[str, object]:
        return kwargs

    async def apply_page(self, page: object) -> None:
        del page


class _ProxyContext:
    proxy_available = True
    residential_proxy_available = True
    current_proxy_url = "http://proxy.example:8080"

    def __init__(self) -> None:
        self.routes: list[tuple[str, str]] = []
        self.rotations = 0

    def set_effective_route(self, *, tier: str, network: str) -> None:
        self.routes.append((tier, network))

    def rotate_proxy(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.rotations += 1


@pytest.fixture(autouse=True)
def fast_bypass_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep policy tests independent from optional browser runtime startup."""
    monkeypatch.setattr(adaptive, "resolve_bypass", lambda *args, **kwargs: _Strategy())
    capabilities = {
        "noop": BypassCapability(cost=0),
        "curl_stealth": BypassCapability(
            cost=10,
            challenge_actions=frozenset({"tls_impersonation"}),
        ),
        "tls_client": BypassCapability(
            cost=12,
            challenge_actions=frozenset({"tls_impersonation"}),
        ),
        "stealth_browser": BypassCapability(
            cost=20,
            browser_family="chromium",
            challenge_actions=frozenset({"passive_js", "generic_challenge"}),
            min_remaining_seconds=5.0,
        ),
        "patchright_browser": BypassCapability(
            cost=25,
            browser_family="chromium_patchright",
            challenge_actions=frozenset({"fingerprint_resistant", "generic_challenge"}),
            min_remaining_seconds=8.0,
        ),
        "nodriver": BypassCapability(
            cost=30,
            browser_family="chromium_cdp",
            challenge_actions=frozenset({"cdp_checkbox", "cloudflare_challenge"}),
            legal_gate="adr_073",
            min_remaining_seconds=10.0,
        ),
        "camoufox": BypassCapability(
            cost=40,
            browser_family="firefox",
            challenge_actions=frozenset({"fingerprint_resistant", "generic_challenge"}),
            min_remaining_seconds=12.0,
        ),
        "cloak": BypassCapability(
            cost=100,
            browser_family="chromium_patched",
            challenge_actions=frozenset({"fingerprint_resistant", "terminal"}),
            legal_gate="adr_073",
            min_remaining_seconds=20.0,
        ),
    }
    monkeypatch.setattr(adaptive, "get_bypass_capability", capabilities.__getitem__)


def test_adaptive_bypass_starts_at_noop_and_visits_each_tier() -> None:
    manager = AdaptiveBypassManager()
    names = [manager.current_name]
    while manager.escalate():
        names.append(manager.current_name)
    assert names == list(manager.available_tiers)
    assert manager.escalate() is False


def test_conservative_fallback_order_is_explicit() -> None:
    manager = AdaptiveBypassManager()
    assert manager.available_tiers == (
        "noop",
        "curl_stealth",
        "tls_client",
        "stealth_browser",
        "patchright_browser",
        "nodriver",
        "camoufox",
        "cloak",
    )


@pytest.mark.asyncio
async def test_captcha_failure_escalates() -> None:
    manager = AdaptiveBypassManager()
    initial = manager.current_name
    kind = await manager.handle_failure(
        "source", status_code=200, body=b"<div>ddos-guard</div>", error=None
    )
    assert kind is FailureKind.CHALLENGE
    assert manager.current_name != initial or manager.escalations_total > 0


@pytest.mark.asyncio
async def test_solve_page_challenge_uses_monitor_observed_challenge_type() -> None:
    manager = AdaptiveBypassManager()
    solver = type(
        "Solver",
        (),
        {
            "solve": AsyncMock(return_value=CaptchaSolveResult(solved=False, method="provider")),
            "solve_detected": AsyncMock(
                return_value=CaptchaSolveResult(solved=False, method="autodetect")
            ),
        },
    )()
    manager._captcha_solver = solver  # type: ignore[attr-defined]
    manager.set_observed_challenge_type("recaptcha")
    page = object()

    await manager.solve_page_challenge(page, url="https://example.test/jobs")

    solver.solve.assert_awaited_once_with(
        page,
        challenge_type="recaptcha",
        url="https://example.test/jobs",
    )
    solver.solve_detected.assert_not_awaited()


@pytest.mark.asyncio
async def test_persistent_browser_profile_is_not_deleted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from job_ftch.config import get_settings

    profile_dir = tmp_path / "warm-profile"
    monkeypatch.setenv("JOB_FTCH_BROWSER_PROFILE_DIR", str(profile_dir))
    monkeypatch.setenv("JOB_FTCH_BROWSER_PROFILE_PERSISTENT", "true")
    get_settings.cache_clear()
    try:
        manager = AdaptiveBypassManager()

        prepared = manager.prepare_browser_config({"persistent_context": True})
        await manager.close()

        assert Path(prepared["_profile_dir"]) == profile_dir
        assert profile_dir.exists()
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_browser_session_state_reuses_clearance_cookie_and_user_agent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from job_ftch.config import get_settings

    state_dir = tmp_path / "session-states"
    monkeypatch.setenv("JOB_FTCH_BROWSER_SESSION_STATE_ENABLED", "true")
    monkeypatch.setenv("JOB_FTCH_BROWSER_SESSION_STATE_DIR", str(state_dir))
    get_settings.cache_clear()
    try:
        manager = AdaptiveBypassManager()
        manager.bind_context(SimpleNamespace(domain="example.test"))
        page = SimpleNamespace(
            url="https://example.test/jobs",
            evaluate=AsyncMock(return_value="Pinned UA"),
            context=SimpleNamespace(
                cookies=AsyncMock(
                    return_value=[
                        {
                            "name": "cf_clearance",
                            "value": "secret-cookie",
                            "domain": "example.test",
                            "path": "/",
                        },
                        {
                            "name": "tracking_cookie",
                            "value": "ignored",
                            "domain": "example.test",
                            "path": "/",
                        },
                    ]
                )
            ),
        )

        await manager.capture_session_state(page)

        next_manager = AdaptiveBypassManager()
        next_manager.bind_context(SimpleNamespace(domain="example.test"))
        prepared = next_manager.prepare_browser_config({})

        assert prepared["user_agent"] == "Pinned UA"
        assert [cookie["name"] for cookie in prepared["cookies"]] == ["cf_clearance"]
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_rate_limit_is_classified_without_forced_escalation() -> None:
    manager = AdaptiveBypassManager()
    assert (
        await manager.handle_failure(
            "source", status_code=429, body=b"Too Many Requests", error=None
        )
        is FailureKind.RATE_LIMIT
    )


@pytest.mark.asyncio
async def test_rate_limit_activates_proxy_without_changing_engine() -> None:
    manager = AdaptiveBypassManager()
    context = _ProxyContext()
    manager.bind_context(context)
    initial = manager.current_name
    await manager.handle_failure("source", status_code=429)

    assert manager.current_name == initial
    assert manager.uses_proxy
    assert manager.current_proxy_url == "http://proxy.example:8080"


@pytest.mark.asyncio
async def test_proxy_route_survives_engine_transition() -> None:
    manager = AdaptiveBypassManager()
    manager.bind_context(_ProxyContext())
    await manager.handle_failure("source", status_code=429)

    await manager.handle_failure(
        "source",
        status_code=403,
        body=b"cloudflare turnstile challenge",
    )

    assert manager.current_name == "nodriver"
    assert manager.uses_proxy


@pytest.mark.asyncio
async def test_tls_failure_changes_transport_only() -> None:
    manager = AdaptiveBypassManager()

    kind = await manager.handle_failure("source", error=RuntimeError("TLS error"))

    assert kind is FailureKind.TLS_ERROR
    assert manager.current_name == "curl_stealth"
    assert not manager.uses_proxy


@pytest.mark.asyncio
async def test_parser_and_server_errors_do_not_change_route() -> None:
    manager = AdaptiveBypassManager()
    initial = manager.route_state

    await manager.handle_failure("source", error=ValueError("bad selector"))
    await manager.handle_failure("source", status_code=503)

    assert manager.route_state == initial


@pytest.mark.asyncio
async def test_fingerprint_signal_selects_patchright_before_heavier_browsers() -> None:
    manager = AdaptiveBypassManager()

    kind = await manager.handle_failure(
        "source",
        status_code=403,
        body=b"Automated browser: browser fingerprint rejected",
    )

    assert kind is FailureKind.BLOCKED_FINGERPRINT
    assert manager.current_name == "patchright_browser"


@pytest.mark.asyncio
async def test_expensive_browser_is_skipped_when_deadline_is_too_close() -> None:
    manager = AdaptiveBypassManager()
    async with source_deadline_scope(asyncio.get_running_loop().time() + 4.0):
        await manager.handle_failure(
            "source",
            status_code=403,
            body=b"Automated browser: browser fingerprint rejected",
        )

    assert manager.current_name == "noop"


@pytest.mark.asyncio
async def test_direct_target_cannot_bypass_legal_gate() -> None:
    manager = AdaptiveBypassManager({"allow_adr_073": False})

    assert manager.escalate_to("nodriver") is False
    assert manager.escalate_to("cloak") is False
    assert manager.current_name == "noop"


@pytest.mark.asyncio
async def test_persistent_profile_is_source_scoped_and_cleaned() -> None:
    manager = AdaptiveBypassManager()
    first = manager.prepare_browser_config({"persistent_context": True})
    second = manager.prepare_browser_config({"persistent_context": True})
    profile = Path(first["_profile_dir"])

    assert second["_profile_dir"] == str(profile)
    assert profile.is_dir()

    await manager.close()
    assert not profile.exists()


@pytest.mark.asyncio
async def test_ok_response_keeps_current_tier() -> None:
    manager = AdaptiveBypassManager()
    initial = manager.current_name
    assert (
        await manager.handle_failure(
            "source", status_code=200, body=b"<html>Normal page</html>", error=None
        )
        is FailureKind.OK
    )
    assert manager.current_name == initial
    assert manager.escalations_total == 0


@pytest.mark.asyncio
async def test_apply_page_calls_strategy_and_behavior_simulator() -> None:
    manager = AdaptiveBypassManager()
    manager._current_strategy.apply_page = AsyncMock()
    manager._behavior_sim.apply_page = AsyncMock()
    await manager.apply_page(object())
    manager._current_strategy.apply_page.assert_called_once()
    manager._behavior_sim.apply_page.assert_called_once()
    assert "behavior_sim" not in manager.available_tiers


@pytest.mark.asyncio
async def test_apply_page_installs_recaptcha_action_probe() -> None:
    manager = AdaptiveBypassManager()
    page = type(
        "Page",
        (),
        {
            "add_init_script": AsyncMock(),
        },
    )()

    await manager.apply_page(page)

    scripts = [call.args[0] for call in page.add_init_script.await_args_list]
    assert any("__job_ftch_recaptcha_executes" in script for script in scripts)
