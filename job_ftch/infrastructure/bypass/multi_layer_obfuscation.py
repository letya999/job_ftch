"""Multi-layer fingerprint obfuscation orchestrator (ADR-076).

Applies defense-in-depth by coordinating multiple obfuscation layers
in priority order. Each layer is a self-contained module that can be
used independently or composed via this orchestrator.

The orchestrator does not import layer implementations at module level —
layers are registered lazily to avoid import cycles and to keep layers
truly optional (uninstalled extras simply have no registered layer).
"""

from __future__ import annotations

import contextlib
import hashlib
import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import structlog

logger = structlog.get_logger("job_ftch.bypass.multi_layer_obfuscation")


def _stable_seed(*parts: str) -> int:
    """Deterministic per-session seed so layer artifacts stay coherent."""
    digest = hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


async def _maybe_await(value: Any) -> Any:
    """Await a value if it is awaitable (page hooks differ across engines)."""
    if inspect.isawaitable(value):
        return await value
    return value


@dataclass(slots=True)
class ObfuscationContext:
    """Shared context passed through all obfuscation layers."""

    domain: str
    persona_name: str
    browser_family: str
    transport: str  # "http" | "browser"
    page: Any = None  # playwright Page when available
    applied_layers: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LayerResult:
    """Result of applying a single obfuscation layer."""

    layer_name: str
    applied: bool
    skipped_reason: str = ""
    duration_ms: float = 0.0


@runtime_checkable
class ObfuscationLayer(Protocol):
    """Protocol for a single obfuscation layer."""

    @property
    def name(self) -> str: ...

    @property
    def priority(self) -> int: ...

    async def apply(self, context: ObfuscationContext) -> LayerResult: ...

    def is_applicable(self, context: ObfuscationContext) -> bool: ...


class MultiLayerObfuscation:
    """Orchestrate defense-in-depth across multiple obfuscation layers.

    Usage::

        orchestrator = MultiLayerObfuscation()
        orchestrator.register_layer(JSStealthLayer())
        orchestrator.register_layer(NetworkFingerprintLayer())

        ctx = ObfuscationContext(
            domain="example.com",
            persona_name="chrome_131_win_a",
            browser_family="chromium",
            transport="browser",
        )
        results = await orchestrator.apply_all(ctx)
    """

    def __init__(
        self,
        layers: list[ObfuscationLayer] | None = None,
    ) -> None:
        self._layers: list[ObfuscationLayer] = []
        if layers:
            for layer in layers:
                self.register_layer(layer)

    def register_layer(self, layer: ObfuscationLayer) -> None:
        """Register a layer and maintain priority order."""
        self._layers.append(layer)
        self._layers.sort(key=lambda layer: layer.priority)

    async def apply_all(
        self,
        context: ObfuscationContext,
    ) -> list[LayerResult]:
        """Apply all applicable layers in priority order."""
        results: list[LayerResult] = []
        for layer in self._layers:
            if not layer.is_applicable(context):
                results.append(
                    LayerResult(
                        layer_name=layer.name,
                        applied=False,
                        skipped_reason="not_applicable",
                    )
                )
                continue

            start = time.monotonic()
            try:
                result = await layer.apply(context)
                elapsed_ms = (time.monotonic() - start) * 1000
                # Override duration with our measurement.
                result = LayerResult(
                    layer_name=result.layer_name,
                    applied=result.applied,
                    skipped_reason=result.skipped_reason,
                    duration_ms=round(elapsed_ms, 2),
                )
                if result.applied:
                    context.applied_layers.append(layer.name)
            except Exception as exc:
                elapsed_ms = (time.monotonic() - start) * 1000
                logger.warning(
                    "obfuscation_layer_error",
                    layer=layer.name,
                    error=str(exc),
                    duration_ms=round(elapsed_ms, 2),
                )
                result = LayerResult(
                    layer_name=layer.name,
                    applied=False,
                    skipped_reason=f"error: {exc}",
                    duration_ms=round(elapsed_ms, 2),
                )
            results.append(result)

        logger.debug(
            "multi_layer_applied",
            total_layers=len(self._layers),
            applied_count=sum(1 for r in results if r.applied),
            skipped_count=sum(1 for r in results if not r.applied),
            applied_names=context.applied_layers,
        )
        return results

    def get_applicable_layers(
        self,
        context: ObfuscationContext,
    ) -> list[ObfuscationLayer]:
        """Return layers that would be applied for the given context."""
        return [layer for layer in self._layers if layer.is_applicable(context)]

    @property
    def layer_count(self) -> int:
        return len(self._layers)

    @property
    def layer_names(self) -> list[str]:
        return [layer.name for layer in self._layers]


# ── Built-in layer stubs ────────────────────────────────────────────
# These are lightweight wrappers that reference existing modules.
# The actual logic lives in the referenced modules; these stubs
# provide the ObfuscationLayer interface for the orchestrator.


class JSStealthLayer:
    """Generates per-session fingerprint overrides via FingerprintGenerator + evolution."""

    @property
    def name(self) -> str:
        return "js_stealth"

    @property
    def priority(self) -> int:
        return 10

    def is_applicable(self, context: ObfuscationContext) -> bool:
        return context.transport == "browser"

    async def apply(self, context: ObfuscationContext) -> LayerResult:
        if context.page is None:
            return LayerResult(layer_name=self.name, applied=False, skipped_reason="no_page")
        from job_ftch.infrastructure.bypass.domain_intel import get_domain_intel
        from job_ftch.infrastructure.bypass.fingerprint_generator import FingerprintGenerator

        intel = get_domain_intel()
        gen = FingerprintGenerator(
            seed=_stable_seed(context.persona_name, context.domain),
            evolution=intel.fingerprint_evolution,
        )
        platform = "windows" if "win" in context.persona_name.lower() else "linux"
        fp = gen.generate(os=platform)
        context.metadata["fingerprint"] = {
            "canvas_seed": fp.canvas_seed,
            "hardware_concurrency_observed": "browser-native",
            "device_memory_observed": "browser-native",
            "screen": f"{fp.screen_width}x{fp.screen_height}",
        }
        # TRACK A5/R1: navigator.hardwareConcurrency/deviceMemory are worker-readable
        # scalars. A page init script would patch only the window realm and create
        # an I8 window-vs-worker leak, so this layer records the generated profile
        # metadata but leaves the browser-native values untouched.
        return LayerResult(layer_name=self.name, applied=True)


class NetworkFingerprintLayer:
    """Records TLS/HTTP fingerprint metadata for coherence auditing."""

    @property
    def name(self) -> str:
        return "network_fingerprint"

    @property
    def priority(self) -> int:
        return 20

    def is_applicable(self, context: ObfuscationContext) -> bool:
        return context.transport == "http"

    async def apply(self, context: ObfuscationContext) -> LayerResult:
        from job_ftch.infrastructure.bypass.domain_intel import get_domain_intel

        intel = get_domain_intel()
        best = intel.get_best_fingerprint()
        context.metadata["network_fingerprint"] = {
            "persona": context.persona_name,
            "browser_family": context.browser_family,
            "evolved_gene_id": getattr(best, "gene_id", None),
            "evolved_fitness": round(getattr(best, "fitness", 0.5), 3),
        }
        return LayerResult(layer_name=self.name, applied=True)


class BehavioralNoiseLayer:
    """Behavioral timing parameters from behavioral_noise.py.

    Publishes seed-derived typing/click timings into ``context.metadata`` so
    interaction code can consume coherent per-session noise. It does not drive
    the page itself: active mouse/scroll simulation is owned by BehaviorSim.
    """

    @property
    def name(self) -> str:
        return "behavioral_noise"

    @property
    def priority(self) -> int:
        return 30

    def is_applicable(self, context: ObfuscationContext) -> bool:
        return context.transport == "browser"

    async def apply(self, context: ObfuscationContext) -> LayerResult:
        from job_ftch.infrastructure.bypass.behavioral_noise import BehavioralNoise

        noise = BehavioralNoise(seed=_stable_seed(context.persona_name, context.domain))
        context.metadata["behavioral"] = {
            "typing_delay": round(noise.typing_delay(), 4),
            "click_duration": round(noise.click_duration(), 4),
        }
        return LayerResult(layer_name=self.name, applied=True)


class TemporalShapingLayer:
    """Temporal pacing recommendation from temporal_shaper.py.

    Records a coherent per-session inter-arrival hint in ``context.metadata``.
    It never sleeps here: request pacing is enforced by the Wave 1 DomainPacer,
    so applying an extra delay on this hot path would double-throttle.
    """

    @property
    def name(self) -> str:
        return "temporal_shaping"

    @property
    def priority(self) -> int:
        return 40

    def is_applicable(self, context: ObfuscationContext) -> bool:
        return True  # Always applicable.

    async def apply(self, context: ObfuscationContext) -> LayerResult:
        from job_ftch.infrastructure.bypass.temporal_shaper import TemporalShaper

        shaper = TemporalShaper(seed=_stable_seed(context.persona_name, context.domain))
        context.metadata["temporal_pacing_seconds"] = round(shaper.inter_arrival(), 3)
        return LayerResult(layer_name=self.name, applied=True)


class PhysicalContextLayer:
    """Geolocation/device coherence from physical_context.py.

    Injects a geolocation-spoofing init script (pre-navigation) so the page's
    reported position matches the persona, and records the coherent
    timezone/locale/city in ``context.metadata``.
    """

    @property
    def name(self) -> str:
        return "physical_context"

    @property
    def priority(self) -> int:
        return 50

    def is_applicable(self, context: ObfuscationContext) -> bool:
        return context.transport == "browser"

    async def apply(self, context: ObfuscationContext) -> LayerResult:
        if context.page is None:
            return LayerResult(layer_name=self.name, applied=False, skipped_reason="no_page")
        from job_ftch.infrastructure.bypass.physical_context import (
            PhysicalContextGenerator,
        )

        country = str(context.metadata.get("proxy_country") or "US")
        physical = PhysicalContextGenerator(
            seed=_stable_seed(context.persona_name, context.domain)
        ).generate(ip_country=country)
        context.metadata["physical_context"] = {
            "timezone": physical.timezone,
            "locale": physical.locale,
            "city": physical.location.city,
            "country": physical.location.country,
        }
        add_init_script = getattr(context.page, "add_init_script", None)
        if not callable(add_init_script):
            return LayerResult(
                layer_name=self.name, applied=False, skipped_reason="page_no_init_script"
            )
        with contextlib.suppress(Exception):
            await _maybe_await(add_init_script(physical.get_geolocation_js()))
            return LayerResult(layer_name=self.name, applied=True)
        return LayerResult(layer_name=self.name, applied=False, skipped_reason="init_script_failed")


class ReferrerChainLayer:
    """Forged inbound referrer from referrer_chain.py.

    Generates a coherent search/social referrer for the source domain and, for
    browser transport, sets it as the ``Referer`` header before navigation so
    traffic looks organically referred rather than direct.
    """

    @property
    def name(self) -> str:
        return "referrer_chain"

    @property
    def priority(self) -> int:
        return 60

    def is_applicable(self, context: ObfuscationContext) -> bool:
        return True  # Always applicable.

    async def apply(self, context: ObfuscationContext) -> LayerResult:
        from job_ftch.infrastructure.bypass.referrer_chain import ReferrerChainGenerator

        domain = context.domain if context.domain and context.domain != "unknown" else "example.com"
        referrer = ReferrerChainGenerator(
            seed=_stable_seed(context.persona_name, context.domain)
        ).get_immediate_referrer(f"https://{domain}/")
        context.metadata["referrer"] = {
            "url": referrer.url,
            "source_type": referrer.source_type,
        }
        if context.transport != "browser" or context.page is None:
            return LayerResult(layer_name=self.name, applied=False, skipped_reason="metadata_only")
        # TRACK B4: scope the forged Referer to the TOP-LEVEL DOCUMENT only.
        # A blanket ``set_extra_http_headers({"Referer": ...})`` pins the same
        # external referrer (e.g. a Google search URL) onto every subresource,
        # which is itself a fingerprint - real browsers derive subresource
        # Referers from their referrer policy. A one-shot route handler rewrites
        # only the document request, then removes itself so subresources keep
        # the browser's native referrer behavior.
        page = context.page
        route = getattr(page, "route", None)
        unroute = getattr(page, "unroute", None)
        if not callable(route) or not callable(unroute):
            return LayerResult(
                layer_name=self.name, applied=False, skipped_reason="page_no_route_api"
            )

        referer_url = referrer.url

        async def _referer_for_document(handler_route: Any, *_args: Any) -> None:
            request = getattr(handler_route, "request", None)
            resource_type = getattr(request, "resource_type", "")
            try:
                if resource_type == "document":
                    headers = dict(getattr(request, "headers", {}) or {})
                    headers["referer"] = referer_url
                    await _maybe_await(handler_route.continue_(headers=headers))
                    with contextlib.suppress(Exception):
                        await _maybe_await(unroute("**/*", _referer_for_document))
                else:
                    await _maybe_await(handler_route.continue_())
            except Exception as exc:  # normal during deadline teardown
                if type(exc).__name__ == "TargetClosedError" or "already handled" in str(exc):
                    return
                with contextlib.suppress(Exception):
                    await _maybe_await(handler_route.continue_())

        with contextlib.suppress(Exception):
            await _maybe_await(route("**/*", _referer_for_document))
            return LayerResult(layer_name=self.name, applied=True)
        return LayerResult(
            layer_name=self.name, applied=False, skipped_reason="route_install_failed"
        )


def build_default_orchestrator() -> MultiLayerObfuscation:
    """Build an orchestrator with all built-in layers registered."""
    orchestrator = MultiLayerObfuscation()
    orchestrator.register_layer(JSStealthLayer())
    orchestrator.register_layer(NetworkFingerprintLayer())
    orchestrator.register_layer(BehavioralNoiseLayer())
    orchestrator.register_layer(TemporalShapingLayer())
    orchestrator.register_layer(PhysicalContextLayer())
    orchestrator.register_layer(ReferrerChainLayer())
    return orchestrator
