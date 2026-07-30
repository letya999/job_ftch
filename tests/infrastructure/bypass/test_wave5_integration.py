"""Wave 5: robots.txt policy gate + returning-user session memory wiring."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


class TestRobotsPolicyGate:
    def test_advisory_default_does_not_block(self) -> None:
        import job_ftch.infrastructure.bypass.preflight as pf

        pf._robots_policy = None  # rebuild with current (default, non-enforcing) settings
        result = pf.run_preflight("https://some-unknown-domain-abc123.com/jobs")
        assert result.robots_blocked is False
        pf._robots_policy = None

    def test_enforced_blocks_disallowed_domain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import job_ftch.infrastructure.bypass.preflight as pf
        from job_ftch.infrastructure.bypass.domain_intel import get_domain_intel
        from job_ftch.infrastructure.bypass.robots_policy import RobotsPolicy

        policy = RobotsPolicy(enforce=True, ats_domains=frozenset(pf._ATS_DOMAINS))
        monkeypatch.setattr(pf, "get_robots_policy", lambda: policy)

        domain = "robots-blocked-test.example"
        get_domain_intel().get(domain).robots_txt_disallowed = True

        result = pf.run_preflight(f"https://{domain}/jobs")
        assert result.robots_blocked is True
        assert result.skip_bypass is True
        assert result.reason.startswith("robots:")

    def test_enforced_exempts_ats_domain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import job_ftch.infrastructure.bypass.preflight as pf
        from job_ftch.infrastructure.bypass.robots_policy import RobotsPolicy

        policy = RobotsPolicy(enforce=True, ats_domains=frozenset(pf._ATS_DOMAINS))
        monkeypatch.setattr(pf, "get_robots_policy", lambda: policy)

        # ATS domains resolve via the dedicated-monitor path and stay unblocked.
        result = pf.run_preflight("https://boards.greenhouse.io/acme")
        assert result.robots_blocked is False


class TestBypassContextRobotsEnforcement:
    def test_robots_blocked_property(self) -> None:
        from job_ftch.infrastructure.bypass.context import BypassContext
        from job_ftch.infrastructure.bypass.persona import select_persona
        from job_ftch.infrastructure.bypass.preflight import PreflightResult

        ctx = BypassContext(
            persona=select_persona("blocked.example"),
            preflight=PreflightResult(
                tier="noop", reason="robots:disallowed_by_robots", robots_blocked=True
            ),
            domain="blocked.example",
        )
        assert ctx.robots_blocked is True

    @pytest.mark.asyncio
    async def test_before_request_raises_when_blocked(self) -> None:
        from job_ftch.infrastructure.bypass.context import BypassContext
        from job_ftch.infrastructure.bypass.persona import select_persona
        from job_ftch.infrastructure.bypass.preflight import PreflightResult
        from job_ftch.infrastructure.bypass.robots_policy import RobotsDisallowedError

        ctx = BypassContext(
            persona=select_persona("blocked.example"),
            preflight=PreflightResult(
                tier="noop", reason="robots:disallowed_by_robots", robots_blocked=True
            ),
            domain="blocked.example",
        )
        with pytest.raises(RobotsDisallowedError):
            await ctx.before_request("https://blocked.example/jobs")

    @pytest.mark.asyncio
    async def test_before_request_ok_when_not_blocked(self) -> None:
        from job_ftch.infrastructure.bypass.context import BypassContext
        from job_ftch.infrastructure.bypass.persona import select_persona
        from job_ftch.infrastructure.bypass.preflight import PreflightResult

        ctx = BypassContext(
            persona=select_persona("allowed.example"),
            preflight=PreflightResult(tier="noop", reason="ok"),
            domain="allowed.example",
        )
        headers = await ctx.before_request("https://allowed.example/jobs")
        assert isinstance(headers, dict)


class TestSessionMemoryWiring:
    def test_disabled_by_default_returns_none(self) -> None:
        from job_ftch.infrastructure.bypass.adaptive import AdaptiveBypassManager

        mgr = AdaptiveBypassManager({}, adaptive_enabled=True)
        assert mgr._persona_session_memory() is None

    def test_persist_and_restore_across_managers(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import job_ftch.infrastructure.bypass.session_memory as sm
        from job_ftch.infrastructure.bypass.adaptive import AdaptiveBypassManager

        monkeypatch.setattr(sm, "_DEFAULT_STORAGE_DIR", str(tmp_path))

        writer = AdaptiveBypassManager({}, adaptive_enabled=True)
        writer._session_memory_enabled = True
        writer._context = SimpleNamespace(persona=SimpleNamespace(name="persona_wave5"))
        memory = writer._persona_session_memory()
        memory.state.cookies = [
            {"name": "cf_clearance", "value": "tok", "domain": "d.example", "path": "/"}
        ]
        memory.save()

        reader = AdaptiveBypassManager({}, adaptive_enabled=True)
        reader._session_memory_enabled = True
        reader._context = SimpleNamespace(persona=SimpleNamespace(name="persona_wave5"))
        prepared = reader.prepare_browser_config({})
        names = [c.get("name") for c in prepared.get("cookies", [])]
        assert "cf_clearance" in names

    def test_disabled_does_not_restore(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        import job_ftch.infrastructure.bypass.session_memory as sm
        from job_ftch.infrastructure.bypass.adaptive import AdaptiveBypassManager
        from job_ftch.infrastructure.bypass.session_memory import SessionMemory

        monkeypatch.setattr(sm, "_DEFAULT_STORAGE_DIR", str(tmp_path))
        seeded = SessionMemory("persona_off", storage_dir=tmp_path)
        seeded.state.cookies = [
            {"name": "cf_clearance", "value": "tok", "domain": "d.example", "path": "/"}
        ]
        seeded.save()

        mgr = AdaptiveBypassManager({}, adaptive_enabled=True)
        # session memory disabled (default): no restore even if a file exists
        mgr._context = SimpleNamespace(persona=SimpleNamespace(name="persona_off"))
        prepared = mgr.prepare_browser_config({})
        assert not prepared.get("cookies")
