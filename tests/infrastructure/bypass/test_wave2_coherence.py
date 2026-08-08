"""Wave 2: Persona coherence — Client Hints, header order, toString guard."""

from __future__ import annotations

import pytest

from job_ftch.infrastructure.bypass.persona import PERSONA_POOL


class TestClientHintsFields:
    def test_all_personas_have_platform_version(self) -> None:
        for p in PERSONA_POOL:
            assert p.platform_version, f"{p.name} missing platform_version"

    def test_platform_version_matches_os(self) -> None:
        for p in PERSONA_POOL:
            if p.sec_ch_ua_platform == '"Windows"':
                assert p.platform_version.startswith(("10.", "15.", "16.")), (
                    f"{p.name}: Windows persona has unexpected platformVersion {p.platform_version}"
                )
            elif p.sec_ch_ua_platform == '"macOS"':
                assert p.platform_version.startswith(("14.", "15.")), (
                    f"{p.name}: macOS persona has unexpected platformVersion {p.platform_version}"
                )
            elif p.sec_ch_ua_platform == '"Linux"':
                assert p.platform_version.startswith("6."), (
                    f"{p.name}: Linux persona has unexpected platformVersion {p.platform_version}"
                )

    def test_architecture_matches_os(self) -> None:
        for p in PERSONA_POOL:
            if p.sec_ch_ua_platform == '"macOS"':
                assert p.architecture == "arm", f"{p.name}: macOS should be arm"
            else:
                assert p.architecture == "x86", f"{p.name}: non-macOS should be x86"

    def test_all_personas_have_bitness(self) -> None:
        for p in PERSONA_POOL:
            assert p.bitness == "64", f"{p.name}: expected 64-bit"


class TestHeaderOrder:
    def test_chromium_header_order_matches_browser(self) -> None:
        chromium = [p for p in PERSONA_POOL if p.browser_family == "chromium"]
        assert chromium, "No chromium personas found"
        p = chromium[0]
        # http_headers_ordered returns the canonical curl_cffi-impersonation
        # order; http_headers() must keep that order (dict insertion order).
        headers = p.http_headers()
        keys = list(headers.keys())
        assert keys[0] == "sec-ch-ua", f"First header should be sec-ch-ua, got {keys[0]}"
        assert keys[1] == "sec-ch-ua-mobile"
        assert keys[2] == "sec-ch-ua-platform"
        ua_idx = keys.index("User-Agent")
        accept_idx = keys.index("Accept")
        lang_idx = keys.index("Accept-Language")
        assert ua_idx < accept_idx < lang_idx, "Header order: UA < Accept < Accept-Language"

    def test_chromium_personas_advertise_high_entropy_client_hints(self) -> None:
        chromium = [p for p in PERSONA_POOL if p.browser_family == "chromium"]
        assert chromium, "No chromium personas found"
        for p in chromium:
            ordered = p.http_headers_ordered()
            names = [name.lower() for name, _ in ordered]
            assert "sec-ch-ua-platform-version" in names, (
                f"{p.name}: missing sec-ch-ua-platform-version (Wave 2.4 requirement #11)"
            )
            assert "sec-ch-ua-arch" in names, f"{p.name}: missing sec-ch-ua-arch"
            assert "sec-ch-ua-bitness" in names, f"{p.name}: missing sec-ch-ua-bitness"
            assert "sec-ch-ua-model" in names, f"{p.name}: missing sec-ch-ua-model"

    def test_chromium_webgl_vendor_consistent_with_renderer(self) -> None:
        from job_ftch.infrastructure.bypass.stealth_hardening import (
            _webgl_vendor_for_renderer,
        )

        assert (
            _webgl_vendor_for_renderer("ANGLE (Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0)")
            == "Google Inc. (Intel)"
        )
        assert _webgl_vendor_for_renderer("Apple GPU") == "Google Inc."
        assert (
            _webgl_vendor_for_renderer("Mesa Intel(R) UHD Graphics 630 (CFL GT2)")
            == "Google Inc. (Intel)"
        )
        assert _webgl_vendor_for_renderer("Google SwiftShader") == "Google Inc."

    def test_safswiftshader_coerces_persona(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from job_ftch.infrastructure.bypass import stealth_hardening
        from job_ftch.infrastructure.bypass.persona import select_persona

        monkeypatch.setenv("JOB_FTCH_FORCE_SWIFTSHADER", "1")
        original = select_persona("swiftshader.test", "chromium")
        coerced = stealth_hardening.coerce_persona_renderer_for_runtime(original)
        assert coerced.webgl_renderer == "Google SwiftShader"

    def test_http_headers_ordered_returns_list(self) -> None:
        chromium = [p for p in PERSONA_POOL if p.browser_family == "chromium"]
        ordered = chromium[0].http_headers_ordered()
        assert isinstance(ordered, list)
        assert all(isinstance(item, tuple) and len(item) == 2 for item in ordered)
        # http_headers() dict view must keep the same order.
        dict_view = chromium[0].http_headers()
        assert list(dict_view.keys()) == [name for name, _ in ordered]

    def test_non_chromium_has_no_client_hints(self) -> None:
        firefox = [p for p in PERSONA_POOL if p.browser_family == "firefox"]
        if not firefox:
            pytest.skip("No firefox personas")
        headers = firefox[0].http_headers()
        assert "sec-ch-ua" not in headers
        for hint in (
            "sec-ch-ua-platform-version",
            "sec-ch-ua-arch",
            "sec-ch-ua-bitness",
        ):
            assert hint not in headers, f"Firefox persona must not advertise {hint}"

    def test_chromium_headers_use_lowercase_ch(self) -> None:
        chromium = [p for p in PERSONA_POOL if p.browser_family == "chromium"]
        headers = chromium[0].http_headers()
        assert "sec-ch-ua" in headers
        assert "Sec-CH-UA" not in headers


class TestFingerprintProfileOrdered:
    """Wave 2.5 — FingerprintProfile exposes ordered headers for curl_cffi."""

    def test_profile_headers_ordered_for_chromium(self) -> None:
        from job_ftch.infrastructure.bypass.fingerprint_profile import FingerprintProfile

        p = [p for p in PERSONA_POOL if p.browser_family == "chromium"][0]
        profile = FingerprintProfile.from_persona(p)
        ordered = profile.http_headers_ordered()
        assert isinstance(ordered, list)
        names = [name for name, _ in ordered]
        assert names[0] == "sec-ch-ua"
        # All four new Client Hints present (high-entropy UA-CH #11).
        assert "sec-ch-ua-platform-version" in names
        assert "sec-ch-ua-arch" in names
        assert "sec-ch-ua-bitness" in names
        assert "sec-ch-ua-model" in names
        # Accept-Language remains last in the canonical Chrome order.
        assert names[-1] == "Accept-Language"

    def test_merge_headers_ordered_appends_unknown_headers(self) -> None:
        from job_ftch.infrastructure.bypass.fingerprint_profile import (
            merge_headers_ordered,
        )

        merged = merge_headers_ordered([("X-Custom", "value"), ("Cookie", "x=y")])
        names = [name for name, _ in merged]
        # Existing profile header takes precedence; X-Custom is appended.
        assert "Cookie" in names
        # Sec-CH-UA from profile stays first.
        assert names[0].lower() == "sec-ch-ua"


class TestClassifySilentBlock:
    """Wave 0.3 — silent_block upgrades parser-empty on known non-empty lists."""

    def test_silent_block_upgrades_parse_empty(self) -> None:
        from job_ftch.infrastructure.bypass.failure_signal import (
            FailureKind,
            FetchOutcome,
            classify_silent_block,
        )

        outcome = FetchOutcome(kind=FailureKind.PARSE_EMPTY, empty=True)
        upgraded = classify_silent_block(outcome, expected_nonempty=True, item_count=0)
        assert upgraded.kind == FailureKind.SILENT_BLOCK
        assert upgraded.empty is True
        assert upgraded.should_escalate is True

    def test_silent_block_upgrades_ok_when_no_items_found(self) -> None:
        from job_ftch.infrastructure.bypass.failure_signal import (
            FailureKind,
            FetchOutcome,
            classify_silent_block,
        )

        outcome = FetchOutcome(kind=FailureKind.OK)
        upgraded = classify_silent_block(outcome, expected_nonempty=True, item_count=0)
        assert upgraded.kind == FailureKind.SILENT_BLOCK

    def test_silent_block_keeps_existing_failure(self) -> None:
        from job_ftch.infrastructure.bypass.failure_signal import (
            FailureKind,
            FetchOutcome,
            classify_silent_block,
        )

        outcome = FetchOutcome(kind=FailureKind.BLOCKED)
        upgraded = classify_silent_block(outcome, expected_nonempty=True, item_count=0)
        assert upgraded.kind == FailureKind.BLOCKED

    def test_silent_block_does_not_upgrade_when_items_found(self) -> None:
        from job_ftch.infrastructure.bypass.failure_signal import (
            FailureKind,
            FetchOutcome,
            classify_silent_block,
        )

        outcome = FetchOutcome(kind=FailureKind.OK)
        upgraded = classify_silent_block(outcome, expected_nonempty=True, item_count=42)
        assert upgraded.kind == FailureKind.OK


class TestFingerprintProfileHeaderOrder:
    def test_profile_header_order(self) -> None:
        from job_ftch.infrastructure.bypass.fingerprint_profile import FingerprintProfile

        p = [p for p in PERSONA_POOL if p.browser_family == "chromium"][0]
        profile = FingerprintProfile.from_persona(p)
        headers = profile.http_headers()
        keys = list(headers.keys())
        assert keys[0] == "sec-ch-ua"
        assert keys[-1] == "Accept-Language"


class TestStealthHardeningClientHints:
    def test_client_hints_js_has_platform_version_placeholder(self) -> None:
        from job_ftch.infrastructure.bypass.stealth_hardening import _CLIENT_HINTS_JS

        assert "platformVersion" in _CLIENT_HINTS_JS
        count = _CLIENT_HINTS_JS.count("%s")
        assert count == 10, f"Expected 10 string placeholders, got {count}"

    def test_chromium_shape_js_no_hardware_override(self) -> None:
        from job_ftch.infrastructure.bypass.stealth_hardening import _CHROMIUM_SHAPE_JS

        assert "hardwareConcurrency" not in _CHROMIUM_SHAPE_JS
        assert "deviceMemory" not in _CHROMIUM_SHAPE_JS


class TestNativeToStringGuard:
    def test_guard_js_exists(self) -> None:
        from job_ftch.infrastructure.bypass.stealth_hardening import (
            _NATIVE_TOSTRING_GUARD_JS,
        )

        assert "Function.prototype.toString" in _NATIVE_TOSTRING_GUARD_JS
        assert "[native code]" in _NATIVE_TOSTRING_GUARD_JS
        assert "__markNative" in _NATIVE_TOSTRING_GUARD_JS

    def test_guard_is_first_script(self) -> None:
        import inspect

        from job_ftch.infrastructure.bypass.stealth_hardening import (
            apply_stealth_hardening,
        )

        source = inspect.getsource(apply_stealth_hardening)
        guard_pos = source.find("_NATIVE_TOSTRING_GUARD_JS")
        canvas_pos = source.find("_CANVAS_NOISE_JS")
        assert guard_pos < canvas_pos, "toString guard must be injected before other patches"
