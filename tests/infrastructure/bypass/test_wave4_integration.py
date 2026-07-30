"""Wave 4: verify obfuscation/temporal modules are genuinely wired, not no-ops."""

from __future__ import annotations

import pytest

from job_ftch.infrastructure.bypass.multi_layer_obfuscation import (
    ObfuscationContext,
    build_default_orchestrator,
)


class _FakePage:
    def __init__(self) -> None:
        self.init_scripts: list[str] = []
        self.headers: dict[str, str] = {}

    async def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)

    async def set_extra_http_headers(self, headers: dict[str, str]) -> None:
        self.headers.update(headers)


class TestBypassContextDomain:
    def test_context_exposes_public_domain(self) -> None:
        from job_ftch.infrastructure.bypass.context import BypassContext
        from job_ftch.infrastructure.bypass.persona import select_persona
        from job_ftch.infrastructure.bypass.preflight import PreflightResult

        ctx = BypassContext(
            persona=select_persona("jobs.example.com"),
            preflight=PreflightResult(tier="noop", reason="test"),
            domain="jobs.example.com",
        )
        # The obfuscation/temporal integration reads ``context.domain``; the
        # private ``_domain`` slot is not enough (was the phantom-attr bug).
        assert ctx.domain == "jobs.example.com"


class TestOrchestratorRealEffects:
    @pytest.mark.asyncio
    async def test_geolocation_script_injected(self) -> None:
        page = _FakePage()
        ctx = ObfuscationContext(
            domain="jobs.example.com",
            persona_name="p1",
            browser_family="chromium",
            transport="browser",
            page=page,
        )
        await build_default_orchestrator().apply_all(ctx)
        assert page.init_scripts, "physical_context layer must inject geolocation JS"
        assert any("geolocation" in s for s in page.init_scripts)

    @pytest.mark.asyncio
    async def test_referer_header_set(self) -> None:
        page = _FakePage()
        ctx = ObfuscationContext(
            domain="jobs.example.com",
            persona_name="p1",
            browser_family="chromium",
            transport="browser",
            page=page,
        )
        await build_default_orchestrator().apply_all(ctx)
        assert page.headers.get("Referer", "").startswith("http")

    @pytest.mark.asyncio
    async def test_metadata_artifacts_populated(self) -> None:
        ctx = ObfuscationContext(
            domain="jobs.example.com",
            persona_name="p1",
            browser_family="chromium",
            transport="browser",
            page=_FakePage(),
        )
        await build_default_orchestrator().apply_all(ctx)
        assert ctx.metadata["temporal_pacing_seconds"] > 0
        assert ctx.metadata["behavioral"]["typing_delay"] > 0
        assert ctx.metadata["physical_context"]["timezone"]
        assert ctx.metadata["referrer"]["url"]

    @pytest.mark.asyncio
    async def test_deterministic_per_session(self) -> None:
        """Same persona+domain yields identical artifacts (coherent session)."""
        results = []
        for _ in range(2):
            ctx = ObfuscationContext(
                domain="jobs.example.com",
                persona_name="p1",
                browser_family="chromium",
                transport="browser",
                page=_FakePage(),
            )
            await build_default_orchestrator().apply_all(ctx)
            results.append(ctx.metadata["referrer"]["url"])
        assert results[0] == results[1]


class TestTemporalGraphWiring:
    def test_domain_intel_exposes_temporal_graph(self) -> None:
        from job_ftch.infrastructure.bypass.domain_intel import get_domain_intel

        intel = get_domain_intel()
        # adaptive.open_page reaches the graph via this singleton attribute;
        # the new code read a non-existent ``context.domain_intel`` (dead guard).
        assert hasattr(intel, "temporal_graph")
        assert intel.temporal_graph is not None

    def test_consistency_check_runs(self) -> None:
        import time

        from job_ftch.infrastructure.bypass.domain_intel import get_domain_intel
        from job_ftch.infrastructure.bypass.temporal_graph import SessionRecord

        graph = get_domain_intel().temporal_graph
        record = SessionRecord(
            session_id="s-wave4",
            persona_id="p1",
            domain="wave4-check.example.com",
            timestamp=time.monotonic(),
        )
        verdict = graph.check_consistency(record)
        assert verdict.consistent is True
        graph.add_session(record)
