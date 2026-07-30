from job_ftch.infrastructure.bypass.stealth_hardening import _CHROMIUM_SHAPE_JS


def test_chromium_shape_js_no_hardware_overrides():
    assert "hardwareConcurrency" not in _CHROMIUM_SHAPE_JS
    assert "deviceMemory" not in _CHROMIUM_SHAPE_JS
