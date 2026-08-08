from __future__ import annotations

import subprocess
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "paritylab" / "static"


def test_probe_modules_are_loaded_before_bootstrap() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    positions = [
        html.index("/static/probes/capabilities.js"),
        html.index("/static/probes/runtime.js"),
        html.index("/static/probes/rendering.js"),
        html.index("/static/probes/vendor.js"),
        html.index("/static/probes/deep.js"),
        html.index("/static/probes/behavior.js"),
        html.index("/static/probe.js"),
    ]
    assert positions == sorted(positions)


def test_probe_modules_have_valid_javascript_syntax() -> None:
    for relative in (
        "probes/capabilities.js",
        "probes/runtime.js",
        "probes/rendering.js",
        "probes/vendor.js",
        "probes/deep.js",
        "probes/behavior.js",
        "probe.js",
    ):
        result = subprocess.run(
            ["node", "--check", str(STATIC / relative)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr


def test_deep_probe_covers_planned_physical_surfaces() -> None:
    source = (STATIC / "probes" / "rendering.js").read_text(encoding="utf-8")
    deep_source = (STATIC / "probes" / "deep.js").read_text(encoding="utf-8")
    assert "requestAdapter" in source
    assert "createComputePipeline" in source
    assert "mapAsync" in source
    assert "GPUMapMode.READ" in source
    assert "measureText" in source
    assert "actualBoundingBoxAscent" in source
    assert "font-variation-settings" in source
    assert "getBoundingClientRect" in source
    assert "mediaCapabilities.decodingInfo" in source
    assert "getComputedStyle" in deep_source


def test_behavior_probe_records_target_wheel_touch_and_pen_shape() -> None:
    source = (STATIC / "probes" / "behavior.js").read_text(encoding="utf-8")
    for field in ("targetRect", "deltaY", "pressure", "tangentialPressure", "tiltX", "twist"):
        assert field in source


def test_finish_button_is_the_cross_engine_finalize_contract() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "paritylab" / "clients" / "playwright_client.py"
    ).read_text(encoding="utf-8")
    assert 'locator("#finish-button")' in source
    assert 'locator("#status-report")' in source
    assert '"finalizing"' in source
    assert "window.__parityLabResult" in source
    assert "fetch(`/api/report/${encodeURIComponent(sid)}" in source
    assert "page.context.request.get" not in source


def test_bootstrap_failures_are_exposed_to_browser_adapters() -> None:
    probe = (STATIC / "probe.js").read_text(encoding="utf-8")
    client = (
        Path(__file__).resolve().parents[1] / "paritylab" / "clients" / "playwright_client.py"
    ).read_text(encoding="utf-8")
    assert "window.__parityLabBootstrapError" in probe
    assert "#finish-button:not([disabled])" in client
    assert "bootstrap-failure.png" in client


def test_bootstrap_stages_have_hard_deadlines() -> None:
    probe = (STATIC / "probe.js").read_text(encoding="utf-8")
    assert 'withTimeout(collectWindowProbe(), 20_000, "window probe")' in probe
    assert 'withTimeout(probeDeep(), 15_000, "deep probe")' in probe
    assert "collectDeep({safe, hashString, hashBytes, normalize})" in probe
    deep = (STATIC / "probes" / "deep.js").read_text(encoding="utf-8")
    assert (
        "probes.collectDeep = async helpers => {\n"
        "    const {safe, hashString, hashBytes, normalize} = helpers"
    ) in deep
