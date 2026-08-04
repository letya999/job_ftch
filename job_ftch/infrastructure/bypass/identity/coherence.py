"""Runtime identity-coherence contract (TRACK A / A3).

Promotes the I1-I9 fingerprint invariants from test-only asserts to a single
runtime checker used by BOTH the offline test suite and the live self-check.
Every check answers one question: does this identity tell one self-consistent
story, or does some axis contradict another and betray a fabricated (scraper)
session? A 2026 WAF/Qrator/Turnstile cross-checks UA vs UA-CH vs
navigator.userAgentData vs TLS vs geo vs the worker realm; a single disagreement
is the leak. This module is where those agreements are enforced.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_CHROME_UA_RE = re.compile(r"Chrome/(\d+)")
_CH_CHROME_RE = re.compile(r'"Google Chrome";v="(\d+)"')
_GREASE_RE = re.compile(r"Not.?A.?Brand", re.IGNORECASE)

# Axes that must be identical in the window and worker realms (I8). A worker
# that reports a different UA/platform/hardware than the window is the classic
# "stealth patched window only" tell.
_WORKER_PARITY_AXES = (
    "userAgent",
    "platform",
    "hardwareConcurrency",
    "deviceMemory",
    "timezone",
    "language",
    "webglVendor",
    "webglRenderer",
)


class IdentityIncoherenceError(RuntimeError):
    """Raised by :func:`assert_coherent` when an identity contradicts itself."""


@dataclass(frozen=True, slots=True)
class CoherenceIssue:
    """One violated invariant."""

    code: str  # e.g. "I1", "I5", "I8"
    axis: str  # short axis name, e.g. "ua_vs_client_hints"
    detail: str


@dataclass(slots=True)
class CoherenceReport:
    """Result of a coherence check."""

    issues: list[CoherenceIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def codes(self) -> list[str]:
        return [issue.code for issue in self.issues]

    def add(self, code: str, axis: str, detail: str) -> None:
        self.issues.append(CoherenceIssue(code=code, axis=axis, detail=detail))

    def raise_if_incoherent(self) -> None:
        if self.issues:
            summary = "; ".join(f"[{i.code}:{i.axis}] {i.detail}" for i in self.issues)
            raise IdentityIncoherenceError(summary)


def _persona_of(obj: Any) -> Any:
    """Accept a SessionIdentity or a raw BrowserPersona interchangeably."""
    return getattr(obj, "persona", obj)


def check_identity(identity_or_persona: Any) -> CoherenceReport:
    """Offline coherence of the DECLARED story (no network, no browser)."""
    persona = _persona_of(identity_or_persona)
    report = CoherenceReport()
    family = str(getattr(persona, "browser_family", ""))
    ua = str(getattr(persona, "ua", ""))
    sec_ch_ua = str(getattr(persona, "sec_ch_ua", ""))
    platform = str(getattr(persona, "navigator_platform", ""))
    renderer = str(getattr(persona, "webgl_renderer", ""))

    # family sanity
    if family not in {"chromium", "firefox", "safari"}:
        report.add("FAM", "browser_family", f"unknown family {family!r}")
    else:
        _check_ua_marks_family(report, family, ua)

    # I1 UA <-> UA client hints (chromium only)
    if family == "chromium":
        _check_ua_vs_client_hints(report, ua, sec_ch_ua)
        # I6 GREASE brand must be present
        if not _GREASE_RE.search(sec_ch_ua):
            report.add("I6", "grease_brand", f"chromium sec-ch-ua lacks GREASE: {sec_ch_ua!r}")
    else:
        # I9 non-chromium never sends UA client hints
        if sec_ch_ua:
            report.add("I9", "client_hints", f"{family} must not send sec-ch-ua: {sec_ch_ua!r}")

    # I5 WebGL renderer <-> OS
    _check_webgl_vs_os(report, renderer, platform)

    # I1 (transport) curl impersonation family must match the UA family
    _check_transport_family(report, persona, family)

    return report


def _check_ua_marks_family(report: CoherenceReport, family: str, ua: str) -> None:
    if family == "chromium" and "Chrome/" not in ua:
        report.add("FAM", "ua_family", f"chromium UA missing Chrome/ token: {ua!r}")
    elif family == "firefox" and "Firefox/" not in ua:
        report.add("FAM", "ua_family", f"firefox UA missing Firefox/ token: {ua!r}")
    elif family == "safari" and ("Safari/" not in ua or "Chrome/" in ua):
        report.add("FAM", "ua_family", f"safari UA not Safari-shaped: {ua!r}")


def _check_ua_vs_client_hints(report: CoherenceReport, ua: str, sec_ch_ua: str) -> None:
    ua_major = _CHROME_UA_RE.search(ua)
    ch_major = _CH_CHROME_RE.search(sec_ch_ua)
    if ua_major is None:
        report.add("I1", "ua_vs_client_hints", f"no Chrome major in UA: {ua!r}")
        return
    if ch_major is None:
        report.add(
            "I1", "ua_vs_client_hints", f"no Google Chrome brand in sec-ch-ua: {sec_ch_ua!r}"
        )
        return
    if ua_major.group(1) != ch_major.group(1):
        report.add(
            "I1",
            "ua_vs_client_hints",
            f"UA major {ua_major.group(1)} != client-hints major {ch_major.group(1)}",
        )


def _check_webgl_vs_os(report: CoherenceReport, renderer: str, platform: str) -> None:
    if not renderer:
        return
    if "Apple GPU" in renderer and platform != "MacIntel":
        report.add("I5", "webgl_vs_os", f"Apple GPU on non-Mac platform {platform!r}")
    elif renderer.startswith("ANGLE") and platform != "Win32":
        report.add("I5", "webgl_vs_os", f"ANGLE renderer on non-Windows platform {platform!r}")
    elif renderer.startswith("Mesa") and platform != "Linux x86_64":
        report.add("I5", "webgl_vs_os", f"Mesa renderer on non-Linux platform {platform!r}")


def _check_transport_family(report: CoherenceReport, persona: Any, family: str) -> None:
    try:
        from job_ftch.infrastructure.bypass.fingerprint_profile import FingerprintProfile

        target = FingerprintProfile.from_persona(persona).curl_impersonate
    except Exception as exc:  # pragma: no cover - defensive
        report.add("I1", "transport_family", f"could not derive impersonation: {exc}")
        return
    expected = {"chromium": "chrome", "firefox": "firefox", "safari": "safari"}.get(family)
    if expected and not str(target).startswith(expected):
        report.add(
            "I1",
            "transport_family",
            f"curl impersonation {target!r} does not match {family} family",
        )


def cross_check_observed(
    identity_or_persona: Any,
    *,
    window: dict[str, Any],
    worker: dict[str, Any],
) -> CoherenceReport:
    """Coherence of LIVE observed values (window vs worker vs declared).

    ``window``/``worker`` are the property bags read by the self-check page in
    each realm. This enforces I8 (window==worker) and that the observed values
    match the declared identity, which is where a stealth patch that only touches
    the window realm gets caught.
    """
    report = check_identity(identity_or_persona)
    persona = _persona_of(identity_or_persona)

    # I8: window and worker must agree on every shared axis.
    for axis in _WORKER_PARITY_AXES:
        win = window.get(axis)
        wrk = worker.get(axis)
        if win is not None and wrk is not None and win != wrk:
            report.add("I8", f"worker_parity:{axis}", f"window {win!r} != worker {wrk!r}")

    # Observed window values must match the declared identity.
    declared_ua = str(getattr(persona, "ua", ""))
    observed_ua = str(window.get("userAgent", ""))
    if observed_ua and declared_ua and observed_ua != declared_ua:
        report.add("OBS", "ua", f"observed UA {observed_ua!r} != declared {declared_ua!r}")

    declared_platform = str(getattr(persona, "navigator_platform", ""))
    observed_platform = str(window.get("platform", ""))
    if observed_platform and declared_platform and observed_platform != declared_platform:
        report.add(
            "OBS",
            "platform",
            f"observed platform {observed_platform!r} != declared {declared_platform!r}",
        )

    declared_hc = getattr(persona, "hardware_concurrency", None)
    observed_hc = window.get("hardwareConcurrency")
    if observed_hc is not None and declared_hc is not None and int(observed_hc) != int(declared_hc):
        report.add(
            "OBS",
            "hardware_concurrency",
            f"observed hardwareConcurrency {observed_hc} != declared {declared_hc}",
        )

    return report


def assert_coherent(identity_or_persona: Any) -> None:
    """Raise :class:`IdentityIncoherenceError` if the identity is incoherent."""
    check_identity(identity_or_persona).raise_if_incoherent()
