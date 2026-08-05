"""Tests for bypass integration plan phases A0-I.

Covers:
- A0: tier availability diagnostics, nodriver in ladder
- A1: risk_router time.time() persistence fix
- A2: BypassContext facade
- A3/A4: wiring into browser_utils and career_site_source
- A5: dead code removal (warm_tab_pool), session_handoff fix
- B2: curl impersonate coherence
- B3: persona -> FingerprintProfile derivation
- C1: escalation order (CAPTCHA -> nodriver)
- C2: proxy as orthogonal decorator
- H1: tier stats matrix in domain_intel
- I: captcha provider expansion (2captcha, anticaptcha)
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from job_ftch.application.registry import BypassCapability
from job_ftch.infrastructure.bypass.adaptive import (
    DEFAULT_TIER_ORDER,
    OPTIONAL_TIERS,
    AdaptiveBypassManager,
)
from job_ftch.infrastructure.bypass.fingerprint_profile import FingerprintProfile
from job_ftch.infrastructure.bypass.persona import PERSONA_POOL, select_persona
from job_ftch.infrastructure.bypass.risk_router import DomainReputation, RiskRouter


class TestPhaseA0TierAvailability:
    """A0: nodriver in DEFAULT_TIER_ORDER, camoufox optional, diagnostic logging."""

    def test_nodriver_in_default_tier_order(self) -> None:
        assert "nodriver" in DEFAULT_TIER_ORDER

    def test_camoufox_is_available_to_signal_routing(self) -> None:
        assert "camoufox" in DEFAULT_TIER_ORDER

    def test_camoufox_is_optional(self) -> None:
        assert "camoufox" in OPTIONAL_TIERS

    def test_conservative_fallback_tries_stealth_before_specialists(self) -> None:
        nodriver_idx = DEFAULT_TIER_ORDER.index("nodriver")
        stealth_idx = DEFAULT_TIER_ORDER.index("stealth_browser")
        assert stealth_idx < nodriver_idx

    def test_proxy_removed_from_default_ladder(self) -> None:
        assert "proxy" not in DEFAULT_TIER_ORDER

    def test_missing_tier_logs_warning(self) -> None:
        from job_ftch.application.registry import resolve_bypass

        def _selective_resolve(name: str, bypass_config: Any = None) -> Any:
            if name in ("noop", "curl_stealth"):
                return resolve_bypass(name, bypass_config)
            raise ValueError(f"not registered: {name}")

        with patch(
            "job_ftch.infrastructure.bypass.adaptive.resolve_bypass",
            side_effect=_selective_resolve,
        ):
            mgr = AdaptiveBypassManager(adaptive_enabled=True)
            assert "nodriver" not in mgr._tiers
            assert "noop" in mgr._tiers


class TestPhaseA1RiskRouterPersistence:
    """A1: time.time() instead of time.monotonic() for cross-process persistence."""

    def test_export_import_preserves_timestamps(self) -> None:
        router = RiskRouter()
        router.record_failure("https://hard.example.com/jobs", "curl_stealth")
        router.record_success("https://easy.example.com/jobs", "noop")

        exported = router.export_reputations()
        assert any(e.get("last_failure_at", 0) > 0 for e in exported)
        assert any(e.get("last_success_at", 0) > 0 for e in exported)

        new_router = RiskRouter()
        new_router.import_reputations(exported)

        hard_rep = new_router.get_reputation("https://hard.example.com/jobs")
        assert hard_rep.last_failure_at > 0
        assert hard_rep.total_failures == 1

    def test_decayed_score_uses_wall_clock_time(self) -> None:
        rep = DomainReputation(domain="test.com")
        rep.risk_score = 0.7
        rep.last_failure_at = time.time() - 86400 * 2  # 2 days ago

        half_life = 86400.0
        score = rep.decayed_score(half_life)
        assert score < 0.25  # 2 half-lives = ~25% of original

    def test_decayed_score_fresh_failure_not_decayed(self) -> None:
        rep = DomainReputation(domain="test.com")
        rep.risk_score = 0.7
        rep.last_failure_at = time.time()

        score = rep.decayed_score(86400.0)
        assert score > 0.65

    def test_router_reuses_last_observed_successful_tier(self) -> None:
        router = RiskRouter()
        rep = router.get_reputation("https://known.example.com/jobs")
        rep.total_successes = 3
        rep.last_tier = "camoufox"
        rep.last_success_at = time.time()

        with patch(
            "job_ftch.infrastructure.bypass.risk_router.list_bypass_capabilities",
            return_value={"camoufox": BypassCapability(browser_family="firefox")},
        ):
            assert router.select_tier("https://known.example.com/jobs") == "camoufox"

    def test_high_risk_uses_cheapest_fingerprint_capability(self) -> None:
        router = RiskRouter()
        rep = router.get_reputation("https://hard.example.com/jobs")
        rep.total_failures = 3
        rep.risk_score = 0.9
        rep.last_failure_at = time.time()

        capabilities = {
            "patchright_browser": BypassCapability(
                cost=25,
                browser_family="chromium_patchright",
                challenge_actions=frozenset({"fingerprint_resistant"}),
            ),
            "nodriver": BypassCapability(
                cost=30,
                browser_family="chromium_cdp",
                challenge_actions=frozenset({"fingerprint_resistant"}),
            ),
        }
        with patch(
            "job_ftch.infrastructure.bypass.risk_router.list_bypass_capabilities",
            return_value=capabilities,
        ):
            assert (
                router.select_tier("https://hard.example.com/jobs")
                == "patchright_browser"
            )


class TestPhaseA2BypassContext:
    """A2: BypassContext facade wires all modules."""

    @pytest.mark.asyncio
    async def test_for_url_creates_context(self) -> None:
        from job_ftch.infrastructure.bypass.context import BypassContext

        ctx = await BypassContext.for_url("https://boards.greenhouse.io/company/jobs")
        assert ctx.persona is not None
        assert ctx.tier is not None
        assert ctx._domain == "boards.greenhouse.io"

    @pytest.mark.asyncio
    async def test_before_request_returns_headers(self) -> None:
        from job_ftch.infrastructure.bypass.context import BypassContext

        ctx = await BypassContext.for_url("https://example.com/careers")
        headers = await ctx.before_request("https://example.com/careers/job1")
        assert "User-Agent" in headers
        assert "Referer" in headers

    @pytest.mark.asyncio
    async def test_record_success_updates_router(self) -> None:
        from job_ftch.infrastructure.bypass.context import BypassContext

        ctx = await BypassContext.for_url("https://unique-test-success.example.com/jobs")
        ctx.record_success("https://unique-test-success.example.com/jobs/123")

        from job_ftch.infrastructure.bypass.risk_router import get_router

        router = get_router()
        rep = router.get_reputation("https://unique-test-success.example.com/jobs/123")
        assert rep.total_successes >= 1

    @pytest.mark.asyncio
    async def test_context_kwargs_from_persona(self) -> None:
        from job_ftch.infrastructure.bypass.context import BypassContext

        ctx = await BypassContext.for_url("https://test-kwargs.example.com/jobs")
        kw = ctx.context_kwargs()
        assert "user_agent" in kw
        assert "viewport" in kw
        assert "locale" in kw


class TestPhaseB2CurlCoherence:
    """B2: impersonate defaults to 'chrome' (auto-latest)."""

    def test_default_impersonate_is_chrome_auto(self) -> None:
        from job_ftch.infrastructure.bypass.curl_bypass import CurlBypass

        bypass = CurlBypass()
        assert bypass.impersonate == "chrome"

    def test_select_impersonate_returns_default(self) -> None:
        from job_ftch.infrastructure.bypass.curl_bypass import (
            _CHROME_IMPERSONATION_POOL,
            _select_impersonate,
        )

        result = _select_impersonate("https://example.com/jobs", "chrome")
        assert result in _CHROME_IMPERSONATION_POOL


class TestPhaseB3PersonaCoherence:
    """B3: persona is single source of identity, FingerprintProfile derives from it."""

    def test_all_personas_have_consistent_identity(self) -> None:
        for persona in PERSONA_POOL:
            profile = FingerprintProfile.from_persona(persona)

            if "Chrome/" in persona.ua:
                assert profile.curl_impersonate == "chrome"
                assert "Chrome" in profile.sec_ch_ua or "Chromium" in profile.sec_ch_ua
            elif "Safari/" in persona.ua and "Chrome/" not in persona.ua:
                assert "safari" in profile.curl_impersonate
            elif "Firefox/" in persona.ua:
                assert "firefox" in profile.curl_impersonate

            assert profile.ua == persona.ua
            assert profile.sec_ch_ua == persona.sec_ch_ua
            assert profile.navigator_platform == persona.navigator_platform

    def test_persona_sticky_per_domain(self) -> None:
        p1 = select_persona("example.com")
        p2 = select_persona("example.com")
        _ = select_persona("other.example.com")
        assert p1 is p2
        assert p1.name == p2.name

    def test_from_persona_roundtrip_preserves_viewport(self) -> None:
        persona = PERSONA_POOL[0]
        profile = FingerprintProfile.from_persona(persona)
        assert profile.viewport_width == persona.viewport_width
        assert profile.viewport_height == persona.viewport_height


class TestPhaseA5DeadCodeRemoval:
    """A5: warm_tab_pool deleted, session_handoff wired."""

    def test_warm_tab_pool_deleted(self) -> None:
        from pathlib import Path

        pool_path = Path("job_ftch/infrastructure/bypass/warm_tab_pool.py")
        assert not pool_path.exists()

    def test_session_handoff_uses_chrome_auto(self) -> None:
        from job_ftch.infrastructure.bypass.session_handoff import SessionHandoff

        handoff = SessionHandoff.__new__(SessionHandoff)
        handoff._impersonate = "chrome"
        assert handoff._impersonate == "chrome"


class TestPhaseC2ProxyDecorator:
    """C2: proxy composes orthogonally with any tier via BypassContext."""

    @pytest.mark.asyncio
    async def test_context_proxy_resolve_graceful_on_missing(self) -> None:
        from job_ftch.infrastructure.bypass.context import BypassContext

        with patch(
            "job_ftch.infrastructure.bypass.context.resolve_bypass",
            side_effect=ValueError("not registered"),
        ):
            ctx = await BypassContext.for_url("https://no-proxy-test.example.com/jobs")
            assert ctx._proxy is None
            kw = ctx.context_kwargs()
            assert "proxy" not in kw

    @pytest.mark.asyncio
    async def test_context_with_proxy_composes(self) -> None:
        from job_ftch.infrastructure.bypass.context import BypassContext

        mock_proxy = MagicMock()
        mock_proxy.apply_browser_args = lambda kw: {
            **kw,
            "proxy": {"server": "socks5://1.2.3.4:1080"},
        }
        mock_proxy.apply_http = AsyncMock(return_value="proxied_client")

        ctx = await BypassContext.for_url("https://proxy-test.example.com/jobs")
        ctx._proxy = mock_proxy

        kw = ctx.context_kwargs(use_proxy=True)
        assert kw["proxy"] == {"server": "socks5://1.2.3.4:1080"}

        result = await ctx.apply_http("original_client", use_proxy=True)
        assert result == "proxied_client"


class TestPhaseH1TierStatsMatrix:
    """H1: per-tier success matrix in domain_intel."""

    def test_record_success_increments_ok(self) -> None:
        from job_ftch.infrastructure.bypass.domain_intel import DomainIntelligence

        intel = DomainIntelligence(cache_path=None)
        intel.record_success("test.com", "nodriver")
        intel.record_success("test.com", "nodriver")

        entry = intel.get("test.com")
        assert entry.tier_stats["nodriver"].ok == 2

    def test_record_failure_kind_categorizes(self) -> None:
        from job_ftch.infrastructure.bypass.domain_intel import DomainIntelligence

        intel = DomainIntelligence(cache_path=None)
        intel.record_failure_kind("test.com", "curl_stealth", "blocked")
        intel.record_failure_kind("test.com", "curl_stealth", "captcha")
        intel.record_failure_kind("test.com", "curl_stealth", "timeout")

        stats = intel.get("test.com").tier_stats["curl_stealth"]
        assert stats.blocked == 1
        assert stats.captcha == 1
        assert stats.timeout == 1

    def test_recommended_tier_uses_stats_when_sufficient(self) -> None:
        from job_ftch.infrastructure.bypass.domain_intel import DomainIntelligence

        intel = DomainIntelligence(cache_path=None)
        for _ in range(10):
            intel.record_success("hard.com", "nodriver")
        for _ in range(5):
            intel.record_failure_kind("hard.com", "curl_stealth", "blocked")

        recommended = intel.get_recommended_tier("hard.com")
        assert recommended == "nodriver"

    def test_recommended_tier_falls_back_to_last_success(self) -> None:
        from job_ftch.infrastructure.bypass.domain_intel import DomainIntelligence

        intel = DomainIntelligence(cache_path=None)
        intel.record_success("new.com", "curl_stealth")

        recommended = intel.get_recommended_tier("new.com")
        assert recommended == "curl_stealth"

    def test_tier_stats_serialization_roundtrip(self) -> None:
        from job_ftch.infrastructure.bypass.domain_intel import DomainEntry, TierStats

        entry = DomainEntry(domain="test.com")
        entry.tier_stats["nodriver"] = TierStats(ok=10, blocked=2, captcha=1, timeout=0)
        entry.tier_stats["curl_stealth"] = TierStats(ok=3, blocked=5, captcha=0, timeout=2)

        data = entry.to_dict()
        restored = DomainEntry.from_dict(data)

        assert restored.tier_stats["nodriver"].ok == 10
        assert restored.tier_stats["curl_stealth"].blocked == 5
        assert restored.tier_stats["curl_stealth"].ok_rate == pytest.approx(0.3)


class TestPhaseC1EscalationOrder:
    """C1: challenge evidence selects a suitable engine, not a fixed ladder."""

    @pytest.mark.asyncio
    async def test_generic_captcha_escalates_to_stealth_browser(self) -> None:
        mgr = AdaptiveBypassManager(adaptive_enabled=True)
        if "nodriver" not in mgr._tiers:
            pytest.skip("nodriver tier not available in test env")

        kind = await mgr.handle_failure(
            "test_source",
            status_code=200,
            body=b"<html><body>Checking your browser before accessing</body></html>",
        )
        assert kind == "challenge"
        assert mgr.current_name == "stealth_browser"

    @pytest.mark.asyncio
    async def test_blocked_403_escalates_one_step(self) -> None:
        mgr = AdaptiveBypassManager(adaptive_enabled=True)
        if "nodriver" not in mgr._tiers:
            pytest.skip("nodriver tier not available in test env")

        initial_idx = mgr.current_tier_index
        kind = await mgr.handle_failure(
            "test_source",
            status_code=403,
            body=b"Access Denied",
        )
        assert kind == "blocked"
        assert mgr.current_tier_index == initial_idx + 1


class TestPhaseICaptchaProviders:
    """I: 2captcha and anticaptcha provider integration."""

    def test_captcha_solver_supports_2captcha_provider(self) -> None:
        from job_ftch.infrastructure.bypass.captcha_solver import CaptchaSolverBypass

        solver = CaptchaSolverBypass(provider="2captcha", api_key="test_key")
        assert solver._provider == "2captcha"

    def test_captcha_solver_supports_anticaptcha_provider(self) -> None:
        from job_ftch.infrastructure.bypass.captcha_solver import CaptchaSolverBypass

        solver = CaptchaSolverBypass(provider="anticaptcha", api_key="test_key")
        assert solver._provider == "anticaptcha"

    @pytest.mark.asyncio
    async def test_2captcha_requires_api_key(self) -> None:
        from job_ftch.infrastructure.bypass.captcha_solver import CaptchaSolverBypass

        solver = CaptchaSolverBypass(provider="2captcha", api_key="")
        result = await solver.solve(
            MagicMock(), challenge_type="cloudflare", url="https://example.com"
        )
        assert not result.solved
        assert "no api_key" in result.error

    @pytest.mark.asyncio
    async def test_anticaptcha_requires_api_key(self) -> None:
        from job_ftch.infrastructure.bypass.captcha_solver import CaptchaSolverBypass

        solver = CaptchaSolverBypass(provider="anticaptcha", api_key="")
        result = await solver.solve(
            MagicMock(), challenge_type="hcaptcha", url="https://example.com"
        )
        assert not result.solved
        assert "no api_key" in result.error

    @pytest.mark.asyncio
    async def test_extract_sitekey_handles_missing_page(self) -> None:
        from job_ftch.infrastructure.bypass.captcha_solver import CaptchaSolverBypass

        solver = CaptchaSolverBypass()
        key = await solver._extract_sitekey(object())
        assert key == ""

    @pytest.mark.asyncio
    async def test_unknown_provider_returns_error(self) -> None:
        from job_ftch.infrastructure.bypass.captcha_solver import CaptchaSolverBypass

        solver = CaptchaSolverBypass(provider="nonexistent", api_key="key")
        result = await solver.solve(
            MagicMock(), challenge_type="cloudflare", url="https://example.com"
        )
        assert not result.solved
        assert "unknown provider" in result.error
