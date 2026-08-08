from __future__ import annotations

from collections.abc import Mapping

from paritylab.models import Finding, SessionState, SignalClass
from paritylab.scoring.common import _deep_get, _finding, _realm_map


def _rendering_findings(session: SessionState) -> list[Finding]:
    findings: list[Finding] = []
    deep = _realm_map(session).get("deep")
    if not deep:
        return findings
    extras = deep.get("extras")
    if not isinstance(extras, Mapping) or extras.get("unavailable") is True:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "JS_DEEP_EXTRAS_MISSING",
                "Extended physical-surface probe missing",
                "The modular geometry, WebGPU, media and CSS collector did not submit evidence.",
                realms=["deep"],
            )
        )
        return findings

    geometry = extras.get("geometry")
    if not isinstance(geometry, Mapping) or geometry.get("supported") is not True:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "JS_GEOMETRY_PROBE_FAILED",
                "DOM micro-geometry unavailable",
                "ClientRect, Range and native-control geometry did not produce a stable digest.",
                realms=["deep"],
            )
        )
    elif not geometry.get("digest") or not geometry.get("rects"):
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "JS_GEOMETRY_DIGEST_EMPTY",
                "DOM micro-geometry digest is empty",
                "Geometry collection ran but returned no measurable rectangles or digest.",
                realms=["deep"],
            )
        )

    webgpu = extras.get("webgpu")
    if isinstance(webgpu, Mapping):
        if webgpu.get("supported") is True and webgpu.get("adapter") is False:
            findings.append(
                _finding(
                    SignalClass.LOW,
                    "JS_WEBGPU_ADAPTER_FAILED",
                    "WebGPU exists without an adapter",
                    "navigator.gpu is exposed but requestAdapter returned no adapter.",
                    realms=["deep"],
                )
            )
        elif webgpu.get("supported") is True and webgpu.get("adapter") is True:
            features = webgpu.get("features")
            limits = webgpu.get("limits")
            workload = webgpu.get("workload")
            findings.append(
                _finding(
                    SignalClass.INFO,
                    "JS_WEBGPU_CAPABILITY_CAPTURED",
                    "WebGPU capability shape captured",
                    "Adapter features, limits, identity shape and preferred canvas format were recorded.",
                    evidence={
                        "feature_count": len(features) if isinstance(features, list) else 0,
                        "limit_count": len(limits) if isinstance(limits, Mapping) else 0,
                        "digest": str(webgpu.get("digest", ""))[:24],
                        "workload_digest": (
                            str(workload.get("digest", ""))[:24]
                            if isinstance(workload, Mapping)
                            else ""
                        ),
                    },
                    realms=["deep"],
                )
            )
            if not isinstance(workload, Mapping) or workload.get("supported") is not True:
                findings.append(
                    _finding(
                        SignalClass.LOW,
                        "JS_WEBGPU_WORKLOAD_FAILED",
                        "WebGPU compute readback unavailable",
                        "The adapter was available, but the deterministic compute pipeline did not return mapped output.",
                        evidence={
                            "error": str(workload.get("error", "unavailable"))[:240]
                            if isinstance(workload, Mapping)
                            else "missing workload evidence"
                        },
                        realms=["deep"],
                    )
                )

    media = extras.get("mediaCapabilities")
    if not isinstance(media, Mapping) or not media.get("canPlayType"):
        findings.append(
            _finding(
                SignalClass.LOW,
                "JS_MEDIA_CAPABILITY_MATRIX_EMPTY",
                "Media capability matrix is empty",
                "Codec support and media decoding capability checks returned no usable matrix.",
                realms=["deep"],
            )
        )

    font_rendering = extras.get("fontRendering")
    if not isinstance(font_rendering, Mapping) or font_rendering.get("supported") is not True:
        findings.append(
            _finding(
                SignalClass.LOW,
                "JS_FONT_RENDERING_FAILED",
                "Font rendering fingerprint unavailable",
                "Cross-script glyph metrics and raster output could not be captured.",
                realms=["deep"],
            )
        )
    elif not font_rendering.get("metricsDigest") or not font_rendering.get("rasterDigest"):
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "JS_FONT_RENDERING_DIGEST_EMPTY",
                "Font rendering output is incomplete",
                "The collector returned font data without both metric and raster digests.",
                realms=["deep"],
            )
        )
    else:
        findings.append(
            _finding(
                SignalClass.INFO,
                "JS_FONT_RENDERING_CAPTURED",
                "Font rendering fingerprint captured",
                "Cross-script glyph metrics, fallback availability and raster output were recorded.",
                evidence={
                    "metrics_digest": str(font_rendering.get("metricsDigest"))[:24],
                    "raster_digest": str(font_rendering.get("rasterDigest"))[:24],
                    "family_count": len(font_rendering.get("metrics", {}))
                    if isinstance(font_rendering.get("metrics"), Mapping)
                    else 0,
                },
                realms=["deep"],
            )
        )

    preferences = _deep_get(extras, "runtime.preferences", {})
    if not isinstance(preferences, Mapping) or len(preferences) < 10:
        findings.append(
            _finding(
                SignalClass.LOW,
                "JS_MEDIA_QUERY_MATRIX_SPARSE",
                "CSS media-query matrix is sparse",
                "The extended display, accessibility and input preference matrix is incomplete.",
                evidence={
                    "query_count": len(preferences) if isinstance(preferences, Mapping) else 0
                },
                realms=["deep"],
            )
        )
    return findings
