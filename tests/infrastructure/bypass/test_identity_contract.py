"""TRACK A - the runtime identity-coherence contract and SessionIdentity model.

The persona pool is coherent by contract (check_identity == ok for all); each
targeted mutation trips exactly the invariant it violates; the live self-check
cross-checks window vs worker realms.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from job_ftch.infrastructure.bypass.identity import (
    IdentityIncoherenceError,
    SessionIdentity,
    assert_coherent,
    check_identity,
    cross_check_observed,
)
from job_ftch.infrastructure.bypass.persona import (
    PERSONA_POOL,
    BrowserPersona,
    reset_personas,
    select_persona,
)


@pytest.fixture(autouse=True)
def _isolate_persona_map() -> None:
    reset_personas()
    yield
    reset_personas()


def _first(family: str) -> BrowserPersona:
    return next(p for p in PERSONA_POOL if p.browser_family == family)


class TestPoolIsCoherent:
    @pytest.mark.parametrize("persona", PERSONA_POOL, ids=lambda p: p.name)
    def test_every_persona_is_coherent(self, persona: BrowserPersona) -> None:
        report = check_identity(persona)
        assert report.ok, report.codes


class TestIncoherenceIsCaught:
    def test_ua_client_hints_major_mismatch_is_i1(self) -> None:
        chrome = _first("chromium")
        # Keep the UA major, drop the client-hints major out of sync (GREASE kept
        # so this trips I1, not I6).
        broken = replace(
            chrome,
            sec_ch_ua='"Google Chrome";v="1", "Chromium";v="1", "Not.A/Brand";v="99"',
        )
        report = check_identity(broken)
        assert "I1" in report.codes

    def test_apple_gpu_on_windows_is_i5(self) -> None:
        chrome = _first("chromium")  # a Win32 persona
        broken = replace(chrome, webgl_renderer="Apple GPU")
        assert "I5" in check_identity(broken).codes

    def test_firefox_with_client_hints_is_i9(self) -> None:
        firefox = _first("firefox")
        broken = replace(firefox, sec_ch_ua='"Firefox";v="147"')
        assert "I9" in check_identity(broken).codes

    def test_missing_grease_is_i6(self) -> None:
        chrome = _first("chromium")
        broken = replace(chrome, sec_ch_ua='"Google Chrome";v="146", "Chromium";v="146"')
        assert "I6" in check_identity(broken).codes

    def test_assert_coherent_raises(self) -> None:
        chrome = _first("chromium")
        broken = replace(chrome, webgl_renderer="Apple GPU")
        with pytest.raises(IdentityIncoherenceError):
            assert_coherent(broken)


class TestSessionIdentity:
    @pytest.mark.parametrize("family", ["chromium", "firefox", "safari"])
    def test_for_session_is_coherent(self, family: str) -> None:
        identity = SessionIdentity.for_session(f"{family}.example.com", engine_family=family)
        assert identity.browser_family == family
        assert check_identity(identity).ok
        # projection delegates to the persona
        assert identity.context_kwargs()["user_agent"] == identity.ua

    def test_runtime_alignment_marks_derived_from(self) -> None:
        identity = SessionIdentity.for_session(
            "d.example.com", engine_family="chromium", runtime_version="145.0.0.0"
        )
        assert identity.derived_from == "runtime"
        assert check_identity(identity).ok

    def test_regeneration_bumps_generation_and_stays_coherent(self) -> None:
        identity = SessionIdentity.for_session("d.example.com", engine_family="chromium")
        nxt = identity.with_exit_country("DE")
        assert nxt.generation == identity.generation + 1
        assert check_identity(nxt).ok
        runtime = nxt.with_runtime_version("chromium", "142.0.0.0")
        assert runtime.generation == nxt.generation + 1
        assert check_identity(runtime).ok


class TestCrossCheckObserved:
    def _webgl_vendor(self, renderer: str) -> str:
        if "Apple" in renderer:
            return "Apple Inc."
        if renderer.startswith("Mesa"):
            return "Mesa"
        return "Google Inc. (Intel)"

    def _observed(self, persona: BrowserPersona) -> dict[str, object]:
        return {
            "userAgent": persona.ua,
            "platform": persona.navigator_platform,
            "hardwareConcurrency": persona.hardware_concurrency,
            "deviceMemory": persona.device_memory,
            "timezone": persona.timezone,
            "language": persona.locale,
            "webglVendor": self._webgl_vendor(persona.webgl_renderer),
            "webglRenderer": persona.webgl_renderer,
        }

    def test_matching_window_and_worker_pass(self) -> None:
        persona = select_persona("x.example.com", "chromium")
        obs = self._observed(persona)
        report = cross_check_observed(persona, window=obs, worker=dict(obs))
        assert report.ok, report.codes

    def test_worker_diverging_hardware_is_i8(self) -> None:
        persona = select_persona("x.example.com", "chromium")
        window = self._observed(persona)
        worker = dict(window)
        worker["hardwareConcurrency"] = int(persona.hardware_concurrency) + 4
        report = cross_check_observed(persona, window=window, worker=worker)
        assert "I8" in report.codes

    def test_worker_diverging_ua_is_i8(self) -> None:
        persona = select_persona("x.example.com", "chromium")
        window = self._observed(persona)
        worker = dict(window)
        worker["userAgent"] = persona.ua + " HeadlessChrome"
        report = cross_check_observed(persona, window=window, worker=worker)
        assert "I8" in report.codes

    def test_observed_ua_mismatch_is_obs(self) -> None:
        persona = select_persona("x.example.com", "chromium")
        window = self._observed(persona)
        window["userAgent"] = "totally-different-ua"
        report = cross_check_observed(persona, window=window, worker=dict(window))
        assert "OBS" in report.codes

    def test_observed_timezone_mismatch_is_obs(self) -> None:
        persona = select_persona("x.example.com", "chromium")
        window = self._observed(persona)
        window["timezone"] = "Asia/Tokyo"
        report = cross_check_observed(persona, window=window, worker=dict(window))
        assert any(issue.axis == "timezone" for issue in report.issues)

    def test_observed_language_mismatch_is_obs(self) -> None:
        persona = select_persona("x.example.com", "chromium")
        window = self._observed(persona)
        window["language"] = "ja-JP"
        report = cross_check_observed(persona, window=window, worker=dict(window))
        assert any(issue.axis == "language" for issue in report.issues)

    def test_observed_webgl_mismatch_is_obs(self) -> None:
        persona = select_persona("x.example.com", "chromium")
        window = self._observed(persona)
        window["webglRenderer"] = "Google SwiftShader"
        report = cross_check_observed(persona, window=window, worker=dict(window))
        assert any(issue.axis == "webgl_renderer" for issue in report.issues)
