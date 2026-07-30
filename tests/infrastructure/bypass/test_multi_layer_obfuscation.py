"""Tests for multi-layer fingerprint obfuscation orchestrator (ADR-076)."""

from __future__ import annotations

import pytest

from job_ftch.infrastructure.bypass.multi_layer_obfuscation import (
    LayerResult,
    MultiLayerObfuscation,
    ObfuscationContext,
    build_default_orchestrator,
)


class MockLayer:
    """Test helper: a simple mock obfuscation layer."""

    def __init__(
        self,
        name: str = "mock",
        priority: int = 50,
        applicable: bool = True,
    ) -> None:
        self._name = name
        self._priority = priority
        self._applicable = applicable
        self.apply_called = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def priority(self) -> int:
        return self._priority

    def is_applicable(self, context: ObfuscationContext) -> bool:
        return self._applicable

    async def apply(self, context: ObfuscationContext) -> LayerResult:
        self.apply_called = True
        return LayerResult(layer_name=self._name, applied=True)


class TestMultiLayerObfuscation:
    """Test the orchestrator."""

    @pytest.mark.asyncio
    async def test_register_layer(self) -> None:
        orc = MultiLayerObfuscation()
        orc.register_layer(MockLayer("a", priority=20))
        orc.register_layer(MockLayer("b", priority=10))
        assert orc.layer_count == 2
        # Should be sorted by priority.
        assert orc.layer_names == ["b", "a"]

    @pytest.mark.asyncio
    async def test_apply_all_runs_in_priority_order(self) -> None:
        layers = [
            MockLayer("c", priority=30),
            MockLayer("a", priority=10),
            MockLayer("b", priority=20),
        ]
        orc = MultiLayerObfuscation(layers)
        ctx = ObfuscationContext(
            domain="example.com",
            persona_name="p1",
            browser_family="chromium",
            transport="browser",
        )
        results = await orc.apply_all(ctx)
        names = [r.layer_name for r in results]
        assert names == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_apply_all_skips_non_applicable(self) -> None:
        layers = [
            MockLayer("applicable", priority=10, applicable=True),
            MockLayer("skipped", priority=20, applicable=False),
        ]
        orc = MultiLayerObfuscation(layers)
        ctx = ObfuscationContext(
            domain="example.com",
            persona_name="p1",
            browser_family="chromium",
            transport="browser",
        )
        results = await orc.apply_all(ctx)
        assert results[0].applied is True
        assert results[1].applied is False
        assert results[1].skipped_reason == "not_applicable"

    @pytest.mark.asyncio
    async def test_apply_all_records_results(self) -> None:
        orc = MultiLayerObfuscation([MockLayer("a"), MockLayer("b")])
        ctx = ObfuscationContext(
            domain="example.com",
            persona_name="p1",
            browser_family="chromium",
            transport="browser",
        )
        results = await orc.apply_all(ctx)
        assert len(results) == 2
        assert all(isinstance(r, LayerResult) for r in results)
        assert all(r.duration_ms >= 0 for r in results)

    @pytest.mark.asyncio
    async def test_get_applicable_layers_filters(self) -> None:
        orc = MultiLayerObfuscation(
            [
                MockLayer("browser_only", applicable=True),
                MockLayer("http_only", applicable=False),
            ]
        )
        ctx = ObfuscationContext(
            domain="example.com",
            persona_name="p1",
            browser_family="chromium",
            transport="browser",
        )
        applicable = orc.get_applicable_layers(ctx)
        assert len(applicable) == 1
        assert applicable[0].name == "browser_only"

    @pytest.mark.asyncio
    async def test_empty_orchestrator_returns_empty(self) -> None:
        orc = MultiLayerObfuscation()
        ctx = ObfuscationContext(
            domain="example.com",
            persona_name="p1",
            browser_family="chromium",
            transport="browser",
        )
        results = await orc.apply_all(ctx)
        assert results == []

    @pytest.mark.asyncio
    async def test_context_tracks_applied_layers(self) -> None:
        orc = MultiLayerObfuscation(
            [
                MockLayer("first", priority=10),
                MockLayer("second", priority=20),
                MockLayer("skipped", priority=30, applicable=False),
            ]
        )
        ctx = ObfuscationContext(
            domain="example.com",
            persona_name="p1",
            browser_family="chromium",
            transport="browser",
        )
        await orc.apply_all(ctx)
        assert ctx.applied_layers == ["first", "second"]

    @pytest.mark.asyncio
    async def test_layer_error_is_caught(self) -> None:

        class FailingLayer:
            @property
            def name(self) -> str:
                return "failing"

            @property
            def priority(self) -> int:
                return 10

            def is_applicable(self, context: ObfuscationContext) -> bool:
                return True

            async def apply(self, context: ObfuscationContext) -> LayerResult:
                raise RuntimeError("boom")

        orc = MultiLayerObfuscation()
        orc.register_layer(FailingLayer())  # type: ignore[arg-type]
        orc.register_layer(MockLayer("after_fail", priority=20))
        ctx = ObfuscationContext(
            domain="example.com",
            persona_name="p1",
            browser_family="chromium",
            transport="browser",
        )
        results = await orc.apply_all(ctx)
        assert results[0].applied is False
        assert "error" in results[0].skipped_reason
        # Second layer still runs.
        assert results[1].applied is True


class TestBuildDefaultOrchestrator:
    """Test the default orchestrator factory."""

    def test_has_all_builtin_layers(self) -> None:
        orc = build_default_orchestrator()
        assert orc.layer_count == 6
        expected = [
            "js_stealth",
            "network_fingerprint",
            "behavioral_noise",
            "temporal_shaping",
            "physical_context",
            "referrer_chain",
        ]
        assert orc.layer_names == expected

    @pytest.mark.asyncio
    async def test_browser_context_applies_browser_layers(self) -> None:
        class _FakePage:
            def __init__(self) -> None:
                self.init_scripts: list[str] = []
                self.headers: dict[str, str] = {}

            async def add_init_script(self, script: str) -> None:
                self.init_scripts.append(script)

            async def set_extra_http_headers(self, headers: dict[str, str]) -> None:
                self.headers.update(headers)

        page = _FakePage()
        orc = build_default_orchestrator()
        ctx = ObfuscationContext(
            domain="example.com",
            persona_name="p1",
            browser_family="chromium",
            transport="browser",
            page=page,
        )
        results = await orc.apply_all(ctx)
        applied = [r.layer_name for r in results if r.applied]
        assert "js_stealth" in applied
        assert "behavioral_noise" in applied
        assert "physical_context" in applied
        assert "referrer_chain" in applied
        # Network fingerprint is HTTP-only.
        skipped = [r.layer_name for r in results if not r.applied]
        assert "network_fingerprint" in skipped
        # Layers genuinely applied their effects, not just marker flags.
        assert page.init_scripts, "physical_context must inject a geolocation script"
        assert page.headers.get("Referer"), "referrer_chain must set a Referer header"
        assert "behavioral" in ctx.metadata
        assert "temporal_pacing_seconds" in ctx.metadata
        assert "physical_context" in ctx.metadata
        assert ctx.metadata["referrer"]["url"]

    @pytest.mark.asyncio
    async def test_http_context_applies_http_layers(self) -> None:
        orc = build_default_orchestrator()
        ctx = ObfuscationContext(
            domain="example.com",
            persona_name="p1",
            browser_family="chromium",
            transport="http",
        )
        results = await orc.apply_all(ctx)
        applied = [r.layer_name for r in results if r.applied]
        assert "network_fingerprint" in applied
        assert "temporal_shaping" in applied
        # Browser-only layers skipped.
        skipped = [r.layer_name for r in results if not r.applied]
        assert "js_stealth" in skipped
