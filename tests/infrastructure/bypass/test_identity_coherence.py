"""Fingerprint identity coherence invariants (I1-I9).

These are offline, no-network guards that assert every persona in the pool
tells one self-consistent story about the client it claims to be. Anti-bot
systems in 2026 cross-check UA vs UA-CH vs navigator.userAgentData vs TLS vs
geo; a single disagreement flags fabricated identity. Each test below locks in
one axis of that story so a future edit cannot silently reintroduce a mismatch.
"""

from __future__ import annotations

import re

import pytest

from job_ftch.infrastructure.bypass.fingerprint_profile import FingerprintProfile
from job_ftch.infrastructure.bypass.persona import (
    PERSONA_POOL,
    BrowserPersona,
    reset_personas,
    select_persona,
)

_CHROME_UA_RE = re.compile(r"Chrome/(\d+)")
_CH_CHROME_RE = re.compile(r'"Google Chrome";v="(\d+)"')
_GREASE_RE = re.compile(r"Not.?A.?Brand", re.IGNORECASE)


@pytest.fixture(autouse=True)
def _isolate_persona_map() -> None:
    reset_personas()


@pytest.mark.parametrize("persona", PERSONA_POOL, ids=lambda p: p.name)
def test_i1_chromium_ua_major_matches_client_hints(persona: BrowserPersona) -> None:
    # I1: navigator.userAgent major == sec-ch-ua "Google Chrome" major.
    if persona.browser_family != "chromium":
        return
    ua_major = _CHROME_UA_RE.search(persona.ua)
    ch_major = _CH_CHROME_RE.search(persona.sec_ch_ua)
    assert ua_major is not None, persona.ua
    assert ch_major is not None, persona.sec_ch_ua
    assert ua_major.group(1) == ch_major.group(1)


@pytest.mark.parametrize("persona", PERSONA_POOL, ids=lambda p: p.name)
def test_i6_chromium_client_hints_carry_grease_brand(persona: BrowserPersona) -> None:
    # I6: Chromium always emits a "Not*A*Brand" GREASE entry; its absence is a
    # classic spoofed-brands tell.
    if persona.browser_family != "chromium":
        return
    assert _GREASE_RE.search(persona.sec_ch_ua), persona.sec_ch_ua


@pytest.mark.parametrize("persona", PERSONA_POOL, ids=lambda p: p.name)
def test_i9_non_chromium_never_sends_client_hints(persona: BrowserPersona) -> None:
    # I9: Firefox and Safari do not implement UA Client Hints; a Firefox UA that
    # also exposes sec-ch-ua is incoherent.
    if persona.browser_family == "chromium":
        return
    assert persona.sec_ch_ua == ""


@pytest.mark.parametrize("persona", PERSONA_POOL, ids=lambda p: p.name)
def test_i5_webgl_renderer_matches_os(persona: BrowserPersona) -> None:
    # I5: the WebGL renderer family must be physically possible on the persona's
    # OS (no Direct3D on macOS, no Apple GPU on Windows/Linux).
    renderer = persona.webgl_renderer
    platform = persona.navigator_platform
    if "Apple GPU" in renderer:
        assert platform == "MacIntel"
    elif renderer.startswith("ANGLE"):
        assert platform == "Win32"
    elif renderer.startswith("Mesa"):
        assert platform == "Linux x86_64"


@pytest.mark.parametrize("persona", PERSONA_POOL, ids=lambda p: p.name)
def test_i1_curl_impersonation_family_matches_persona(persona: BrowserPersona) -> None:
    # I1 (transport): the curl_cffi impersonation target family must match the
    # persona's browser family, so the TLS/JA3 fingerprint agrees with the UA.
    profile = FingerprintProfile.from_persona(persona)
    target = profile.curl_impersonate
    if persona.browser_family == "chromium":
        assert target.startswith("chrome")
    elif persona.browser_family == "firefox":
        assert target.startswith("firefox")
    elif persona.browser_family == "safari":
        assert target.startswith("safari")


@pytest.mark.parametrize("family", ["chromium", "firefox", "safari"])
def test_family_matched_selection_never_crosses_engines(family: str) -> None:
    # The persona chosen for an engine family must be of that family, so a
    # Chromium engine never runs a Firefox/Safari identity (defect A5).
    for i in range(60):
        persona = select_persona(f"domain-{i}.example", family)
        assert persona.browser_family == family


def test_for_url_defaults_to_chromium_family() -> None:
    # Fallback path (no engine family known) must default to Chromium, the
    # family every browser engine except camoufox actually runs (defect A5).
    persona = select_persona("unknown-engine.example")
    assert persona.browser_family == "chromium"


def test_i2_timezone_is_not_patched_in_injected_js() -> None:
    # I2: timezone must be owned by the context timezone_id (CDP, all realms),
    # never by a JS shim that patches Intl but not Date.getTimezoneOffset. Guard
    # the regression by asserting the hardening blob contains no Intl timezone
    # override.
    from job_ftch.infrastructure.bypass import stealth_hardening

    source = stealth_hardening.apply_stealth_hardening.__module__
    assert source  # module import guard
    # The scripts list must not reference a timezone override constant.
    module_src = _read_module_source(stealth_hardening)
    assert "_TIMEZONE_JS % timezone" not in module_src


def _read_module_source(module: object) -> str:
    import inspect

    return inspect.getsource(module)  # type: ignore[arg-type]


def test_persona_pool_is_not_stale_against_curl_cffi() -> None:
    # Freshness guard: the pool's newest Chrome major must stay within a small
    # window of curl_cffi's newest supported chrome target, so it cannot rot at
    # Chrome 131 for ~20 months again (defect A6/A11).
    from job_ftch.infrastructure.bypass.curl_bypass import _SUPPORTED_TARGETS

    if not _SUPPORTED_TARGETS:
        pytest.skip("curl_cffi target list unavailable in this environment")
    curl_majors = [
        int(m.group(1))
        for target in _SUPPORTED_TARGETS
        if (m := re.fullmatch(r"chrome(\d+)", target))
    ]
    if not curl_majors:
        pytest.skip("no explicit chrome targets to compare against")
    curl_newest = max(curl_majors)
    pool_newest = max(
        int(browser["version"]) for browser in _pool_browsers() if browser["family"] == "chromium"
    )
    assert pool_newest >= curl_newest - 6, (
        f"persona pool newest Chrome {pool_newest} is stale vs curl_cffi {curl_newest}"
    )


def _pool_browsers() -> list[dict[str, object]]:
    from job_ftch.infrastructure.bypass import persona

    return persona._BROWSERS
